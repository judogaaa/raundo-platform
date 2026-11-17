"""PostgreSQL integration for the users and complaints sections (suite DB only).

Real asyncpg against ``raundo_local_suite``: search by Telegram id/username, profile 360,
audited access restriction with real outbox notification, complaint filters/detail,
resolution with optional target restriction, RBAC, CSRF and stale-state protection.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from raundo.admin_api.security import hash_password
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import (
    AdminAccount,
    AdminAuditLog,
    AdminSession,
    Complaint,
    UserRestriction,
)
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

PASSWORD = "pw-admin-suite-123"


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    url = os.environ.get("DATABASE_URL", "")
    if "raundo_local_suite" not in url:
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres admin suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _dispose_pool_between_loops():  # noqa: ANN202
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _account(factory, *, role: str):  # noqa: ANN001
    account = AdminAccount(
        id=uuid.uuid4(),
        login=f"suite_{uuid.uuid4().hex[:10]}",
        password_hash=hash_password(PASSWORD),
        role=role,
        must_change_password=False,
        mfa_enabled=False,
    )
    async with factory() as session:
        session.add(account)
        await session.commit()
    return account


def _client() -> httpx.AsyncClient:
    from raundo.admin_api.app import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://admin.test")


async def _login(client, login: str):  # noqa: ANN001
    response = await client.post("/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return response.json()


@pytest.mark.asyncio
async def test_users_and_complaints_on_postgres(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    moderator = await _account(factory, role="moderator")
    support = await _account(factory, role="support")

    async with factory() as session:
        accounts = AccountService(session)
        reporter = await accounts.bind_telegram_user(
            telegram_id=772_001,
            username="suite_compl_reporter",
            first_name="Жалобный",
            last_name="Пользователь",
            language_code="ru",
        )
        target = await accounts.bind_telegram_user(
            telegram_id=772_002,
            username="suite_compl_target",
            first_name="Нарушитель",
            last_name=None,
            language_code="ru",
        )
        innocent = await accounts.bind_telegram_user(
            telegram_id=772_003,
            username="suite_untouched",
            first_name="Спокойный",
            last_name=None,
            language_code="ru",
        )
        await session.commit()
        reporter_id, target_id, innocent_id = reporter.id, target.id, innocent.id

    complaint_ids: list[uuid.UUID] = []
    try:
        async with _client() as client:
            await _login(client, owner.login)

            # --- list + search by telegram id / username / name
            listing = await client.get("/users", params={"q": "772001"})
            assert listing.status_code == 200, listing.text
            body = listing.json()
            assert body["items"][0]["id"] == str(reporter_id)
            assert body["items"][0]["telegram_id"] == 772_001
            assert "viewer" in body["items"][0]["profile_types"]
            assert set(body["profile_types"]) == {"founder", "investor", "representative", "viewer"}

            by_username = await client.get("/users", params={"q": "suite_compl_target"})
            assert [i["id"] for i in by_username.json()["items"]] == [str(target_id)]

            by_name = await client.get("/users", params={"q": "Спокойный"})
            assert str(innocent_id) in [i["id"] for i in by_name.json()["items"]]

            filtered = await client.get(
                "/users", params={"profile_type": "investor", "page_size": 5}
            )
            assert filtered.status_code == 200
            assert all("investor" in i["profile_types"] for i in filtered.json()["items"])

            bad_filter = await client.get("/users", params={"profile_type": "alien"})
            assert bad_filter.status_code == 400

            pages = await client.get("/users", params={"page": 1, "page_size": 2, "sort": "name"})
            assert pages.status_code == 200
            assert pages.json()["page_size"] == 2
            assert len(pages.json()["items"]) <= 2

            # --- 360 profile
            detail = await client.get(f"/users/{reporter_id}")
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["can_restrict"] is True
            assert payload["name"] == "Жалобный Пользователь"
            assert payload["startups"] == [] and payload["investor"] is None
            assert "password_hash" not in str(payload)
            assert (await client.get(f"/users/{uuid.uuid4()}")).status_code == 404

            # --- restriction (real effect + audit + outbox)
            no_reason = await client.post(
                f"/users/{target_id}/restrict", json={"reason": "ой"}
            )
            assert no_reason.status_code == 422

            restricted = await client.post(
                f"/users/{target_id}/restrict",
                json={"reason": "Оскорбления в переписке, подтверждено"},
            )
            assert restricted.status_code == 200, restricted.text
            assert restricted.json()["status"] == "restricted"

            again = await client.post(
                f"/users/{target_id}/restrict", json={"reason": "Повторное ограничение"}
            )
            assert again.status_code == 409

            async with factory() as session:
                user = await session.get(User, target_id)
                assert user is not None and user.status == "restricted"
                restriction = await session.scalar(
                    select(UserRestriction).where(
                        UserRestriction.user_id == target_id,
                        UserRestriction.active.is_(True),
                    )
                )
                assert restriction is not None
                audit = await session.scalar(
                    select(AdminAuditLog).where(
                        AdminAuditLog.resource_id == str(target_id),
                        AdminAuditLog.action == "restrict",
                    )
                )
                assert audit is not None and audit.actor_id == owner.id
                event = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.kind == "user.restricted",
                        OutboxEvent.resource_id == str(target_id),
                    )
                )
                assert event is not None and event.state == "pending"
                from raundo.services.outbox_worker import deliver_pending

                sender = FakeTelegram()
                await deliver_pending(session, sender)
                await session.commit()
                assert any("ограничен" in text for _tg, text in sender.sent)
                await session.refresh(event)
                assert event.state == "accepted"

            profile_after = await client.get(f"/users/{target_id}")
            assert profile_after.json()["status"] == "restricted"
            assert profile_after.json()["restrictions"][0]["active"] is True

            # --- complaint intake from the bot service layer
            async with factory() as session:
                from raundo.services.complaints import Evidence, create_complaint

                complaint = await create_complaint(
                    session,
                    reporter_user_id=reporter_id,
                    target_type="user",
                    target_id=str(target_id),
                    reason="Оскорбления и угрозы в личных сообщениях",
                    priority="high",
                    evidence=Evidence(
                        kind="document", file_id="suite-secret-file", name="proof.pdf", size=2048
                    ),
                )
                await session.commit()
                complaint_ids.append(complaint.id)
                number = complaint.public_number

            complaints = await client.get("/complaints", params={"status": "open"})
            assert complaints.status_code == 200
            items = complaints.json()["items"]
            row = next(i for i in items if i["id"] == str(complaint_ids[0]))
            assert row["number"] == number
            assert row["priority"] == "high"
            assert row["target_title"] == "Нарушитель"
            assert row["has_evidence"] is True
            assert complaints.json()["can_decide"] is True

            by_number = await client.get("/complaints", params={"q": number})
            assert [i["id"] for i in by_number.json()["items"]] == [str(complaint_ids[0])]

            by_type = await client.get("/complaints", params={"target_type": "startup"})
            assert all(i["target_type"] == "startup" for i in by_type.json()["items"])
            assert (
                await client.get("/complaints", params={"status": "archived"})
            ).status_code == 400

            view = await client.get(f"/complaints/{complaint_ids[0]}")
            assert view.status_code == 200
            card = view.json()
            assert card["reporter"]["telegram_id"] == 772_001
            assert card["target"]["id"] == str(target_id)
            assert card["target"]["section"] == "users"
            assert card["evidence"] == {"kind": "document", "name": "proof.pdf", "size": 2048}
            assert "suite-secret-file" not in str(card)
            assert card["allowed_statuses"] == ["dismissed", "in_review", "resolved"]
            assert card["can_restrict_target"] is True
            assert (await client.get(f"/complaints/{uuid.uuid4()}")).status_code == 404

            # --- state machine: no comment, duplicate, illegal transition
            missing_comment = await client.patch(
                f"/complaints/{complaint_ids[0]}", json={"status": "resolved"}
            )
            assert missing_comment.status_code == 409

            in_review = await client.patch(
                f"/complaints/{complaint_ids[0]}",
                json={"status": "in_review", "comment": "Взял в работу"},
            )
            assert in_review.status_code == 200, in_review.text
            assert in_review.json()["status"] == "in_review"

            duplicate = await client.patch(
                f"/complaints/{complaint_ids[0]}",
                json={"status": "in_review", "comment": "Повтор"},
            )
            assert duplicate.status_code == 409

            resolved = await client.patch(
                f"/complaints/{complaint_ids[0]}",
                json={"status": "resolved", "comment": "Нарушение подтверждено"},
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["status"] == "resolved"
            assert resolved.json()["resolution_comment"] == "Нарушение подтверждено"

            after = await client.get(f"/complaints/{complaint_ids[0]}")
            assert len(after.json()["history"]) == 2
            assert {h["actor"] for h in after.json()["history"]} == {owner.login}
            assert after.json()["resolution_author"] == owner.login

            # --- RBAC + CSRF
            async with _client() as support_client:
                await _login(support_client, support.login)
                assert (await support_client.get("/users")).status_code == 200
                assert (await support_client.get("/complaints")).status_code == 200
                assert (
                    await support_client.get(f"/complaints/{complaint_ids[0]}")
                ).status_code == 200
                forbidden = await support_client.patch(
                    f"/complaints/{complaint_ids[0]}",
                    json={"status": "in_review", "comment": "нельзя"},
                )
                assert forbidden.status_code == 403
                assert (
                    await support_client.post(
                        f"/users/{innocent_id}/restrict",
                        json={"reason": "Поддержка не должна это делать"},
                    )
                ).status_code == 403

            async with _client() as mod_client:
                await _login(mod_client, moderator.login)
                assert (await mod_client.get("/users")).status_code == 200
                assert (await mod_client.get("/complaints")).status_code == 200
                csrf_missing = await mod_client.patch(
                    f"/complaints/{complaint_ids[0]}",
                    json={"status": "in_review", "comment": "Без CSRF"},
                    headers={"x-csrf-token": "wrong"},
                )
                assert csrf_missing.status_code == 403

            anonymous = _client()
            async with anonymous:
                assert (await anonymous.get("/users")).status_code == 401
                assert (await anonymous.get("/complaints")).status_code == 401
        # Support role is read-only for users but must not see audit-level pages.
        async with _client() as support_client:
            await _login(support_client, support.login)
            assert (await support_client.get("/audit")).status_code == 403
    finally:
        async with factory() as session:
            for complaint_id in complaint_ids:
                await session.execute(delete(Complaint).where(Complaint.id == complaint_id))
            await session.execute(
                delete(Complaint).where(Complaint.reporter_user_id == reporter_id)
            )
            await session.execute(
                delete(UserRestriction).where(UserRestriction.user_id == target_id)
            )
            await session.execute(delete(OutboxEvent).where(OutboxEvent.recipient_user_id.in_(
                [reporter_id, target_id, innocent_id]
            )))
            await session.execute(delete(AdminAuditLog).where(
                AdminAuditLog.resource_id.in_([str(reporter_id), str(target_id)])
            ))
            for user_id in (reporter_id, target_id, innocent_id):
                await session.execute(delete(User).where(User.id == user_id))
            for account in (owner, moderator, support):
                await session.execute(delete(AdminSession).where(AdminSession.admin_id == account.id))
                await session.execute(delete(AdminAccount).where(AdminAccount.id == account.id))
            await session.commit()
