"""Admin MFA API on PostgreSQL: pending setup, audit, gate, disable."""

from __future__ import annotations

import os
import uuid

import httpx
import pyotp
import pytest
from sqlalchemy import delete, select

from raundo.admin_api.security import hash_password
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import AdminAccount, AdminAuditLog, AdminSession
from raundo.testing.db_safety import assert_suite_db_drop_allowed

PASSWORD = "pw-mfa-suite-12345"
LOGIN_PREFIX = "mfa_suite_"


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    url = os.environ.get("DATABASE_URL", "")
    if "raundo_local_suite" not in url:
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        rows = (
            await session.execute(
                select(AdminAccount.id).where(AdminAccount.login.like(f"{LOGIN_PREFIX}%"))
            )
        ).scalars().all()
        if rows:
            await session.execute(delete(AdminSession).where(AdminSession.admin_id.in_(rows)))
            await session.execute(delete(AdminAuditLog).where(AdminAuditLog.actor_id.in_(rows)))
            await session.execute(delete(AdminAccount).where(AdminAccount.id.in_(rows)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _account(factory):  # noqa: ANN001, ANN202
    account = AdminAccount(
        id=uuid.uuid4(),
        login=f"{LOGIN_PREFIX}{uuid.uuid4().hex[:10]}",
        password_hash=hash_password(PASSWORD),
        role="owner",
        must_change_password=False,
        mfa_enabled=False,
    )
    async with factory() as session:
        session.add(account)
        await session.commit()
    return account


def _client() -> httpx.AsyncClient:
    from raundo.admin_api.app import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    )


async def _login(client: httpx.AsyncClient, login: str) -> dict:
    response = await client.post("/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return response.json()


@pytest.mark.asyncio
async def test_pending_setup_is_reused_and_enabled_rejects_setup(factory) -> None:  # noqa: ANN001
    account = await _account(factory)
    async with _client() as client:
        await _login(client, account.login)
        first = await client.post("/auth/mfa/setup")
        assert first.status_code == 200
        secret = first.json()["secret"]
        second = await client.post("/auth/mfa/setup")
        assert second.status_code == 200
        assert second.json()["secret"] == secret  # pending enrolment is resumed

        code = pyotp.TOTP(secret).now()
        enabled = await client.post("/auth/mfa/enable", json={"code": code})
        assert enabled.status_code == 200, enabled.text
        assert len(enabled.json()["backup_codes"]) == 8

        again = await client.post("/auth/mfa/setup")
        assert again.status_code == 409


@pytest.mark.asyncio
async def test_enable_and_disable_are_audited_and_gated(factory) -> None:  # noqa: ANN001
    account = await _account(factory)
    async with _client() as client:
        await _login(client, account.login)
        setup = await client.post("/auth/mfa/setup")
        secret = setup.json()["secret"]

        # Wrong code is rejected and rate-limited counter increments.
        bad = await client.post("/auth/mfa/enable", json={"code": "000000"})
        assert bad.status_code == 400
        # CSRF is required.
        no_csrf = await client.post(
            "/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()},
            headers={"x-csrf-token": ""},
        )
        assert no_csrf.status_code == 403

        ok = await client.post("/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()})
        assert ok.status_code == 200, ok.text
        backup = ok.json()["backup_codes"][0]

        # Fresh password-only session cannot touch protected API.
        async with _client() as fresh:
            await _login(fresh, account.login)
            blocked = await fresh.get("/users")
            assert blocked.status_code == 403
            verified = await fresh.post(
                "/auth/mfa/verify-login", json={"code": pyotp.TOTP(secret).now()}
            )
            assert verified.status_code == 200
            assert (await fresh.get("/users")).status_code == 200

            # Disable requires a valid code; wrong one keeps MFA on.
            wrong = await fresh.post("/auth/mfa/disable", json={"code": "000000"})
            assert wrong.status_code == 400
            disabled = await fresh.post(
                "/auth/mfa/disable", json={"code": pyotp.TOTP(secret).now()}
            )
            assert disabled.status_code == 200, disabled.text

        # Backup code was invalidated together with the secret.
        async with _client() as fresh2:
            await _login(fresh2, account.login)
            reuse = await fresh2.post("/auth/mfa/verify-login", json={"code": backup})
            assert reuse.status_code == 400
            assert (await fresh2.get("/users")).status_code == 200

    async with factory() as session:
        actions = (
            await session.execute(
                select(AdminAuditLog.action).where(
                    AdminAuditLog.actor_id == account.id,
                    AdminAuditLog.action.in_(["mfa_enable", "mfa_disable"]),
                )
            )
        ).scalars().all()
        assert "mfa_enable" in actions and "mfa_disable" in actions
        stored = await session.get(AdminAccount, account.id)
        assert stored is not None
        assert stored.mfa_enabled is False
        assert stored.totp_secret is None
        assert stored.backup_codes_hash is None
