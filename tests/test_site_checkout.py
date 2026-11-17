"""Telegram Login verification and the gated self-service checkout."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.config import Settings
from raundo.db.base import Base
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.age import confirm_age
from raundo.services.consents import ConsentService
from raundo.services.telegram_login import (
    TelegramLoginError,
    issue_session,
    read_session,
    verify_telegram_login,
)

BOT_TOKEN = "123456:TEST-TOKEN-FOR-SITE-LOGIN"


def _settings(**kwargs) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, BOT_TOKEN=BOT_TOKEN, **kwargs)


def _login_payload(**overrides) -> dict[str, str]:  # noqa: ANN003
    data = {
        "id": "555000111",
        "first_name": "Buyer",
        "last_name": "",
        "username": "buyer",
        "auth_date": str(int(time.time())),
    }
    data.update(overrides)
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


def test_valid_login_payload_is_accepted() -> None:
    payload = verify_telegram_login(_login_payload(), _settings())
    assert payload["id"] == "555000111"


def test_tampered_payload_is_rejected() -> None:
    data = _login_payload()
    data["id"] = "999"
    with pytest.raises(TelegramLoginError):
        verify_telegram_login(data, _settings())


def test_expired_login_is_rejected() -> None:
    old = str(int(time.time()) - 3 * 24 * 3600)
    data = _login_payload(auth_date=old)
    with pytest.raises(TelegramLoginError) as exc:
        verify_telegram_login(data, _settings())
    assert exc.value.code == "expired"


def test_session_roundtrip_and_tamper() -> None:
    settings = _settings()
    token = issue_session("user-1", settings)
    assert read_session(token, settings) == "user-1"
    assert read_session(token + "x", settings) is None
    assert read_session(token, Settings(_env_file=None, BOT_TOKEN="999:other")) is None


@pytest.mark.asyncio
async def test_disabled_site_checkout_returns_403() -> None:
    import httpx

    from raundo.admin_api.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://site.test") as client:
        assert (await client.get("/api/public/site/me")).status_code == 403
        assert (await client.post("/api/public/site/session", json=_login_payload())).status_code == 403
        assert (
            await client.post(
                "/api/public/site/orders",
                json={"plan_code": "community_plus", "duration_code": "7d", "accept_offer": True},
            )
        ).status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_enabled_checkout_requires_adult_and_consents(monkeypatch) -> None:
    import httpx

    from raundo.admin_api import site_checkout
    from raundo.admin_api.app import app
    from raundo.config import get_settings
    from tests.test_consents_age_deletion import _user  # noqa: F401  (helper import)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    settings = _settings(
        SITE_CHECKOUT_ENABLED="true", PAYMENTS_MODE="demo", PREMIUM_CATALOG_PUBLIC="true"
    )

    async def _fake_session():  # noqa: ANN202
        async with factory() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[site_checkout._session] = _fake_session

    try:
        async with factory() as session:
            await AccountService(session).bind_telegram_user(
                telegram_id=555000111,
                username="buyer",
                first_name="Buyer",
                last_name=None,
                language_code="ru",
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://site.test"
        ) as client:
            login = await client.post("/api/public/site/session", json=_login_payload())
            assert login.status_code == 200, login.text
            cookie = login.cookies.get(site_checkout.COOKIE)
            assert cookie

            client.cookies.set(site_checkout.COOKIE, cookie)
            order_body = {
                "plan_code": "community_plus",
                "duration_code": "7d",
                "accept_offer": True,
            }

            # Underage user cannot buy.
            minor = await client.post("/api/public/site/orders", json=order_body)
            assert minor.status_code == 403
            assert "18" in minor.text

            async with factory() as session:
                db_user = await session.scalar(select(User).where(User.telegram_id == 555000111))
                confirm_age(db_user, "18_plus")
                await session.commit()

            # Consents still missing.
            no_consent = await client.post("/api/public/site/orders", json=order_body)
            assert no_consent.status_code == 403

            async with factory() as session:
                db_user = await session.scalar(select(User).where(User.telegram_id == 555000111))
                await ConsentService(session).accept_required(db_user.id, source="test")
                await session.commit()

            # Payments are in demo: no live provider, order creation is refused.
            demo = await client.post("/api/public/site/orders", json=order_body)
            assert demo.status_code == 503
            assert "не подключена" in demo.text
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
