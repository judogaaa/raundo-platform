"""Real PostgreSQL integration for the admin console (allowlisted suite DB only).

Covers moderation decisions, support delivery through the outbox with a fake Telegram
transport, organization/mandate review, MFA/session lifecycle, CSRF and RBAC/IDOR.

Runs when the gate sets RAUNDO_POSTGRES_SUITE_TESTS=1 and DATABASE_URL points at
raundo_local_suite (never the live ``raundo`` database).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pyotp
import pytest
from sqlalchemy import delete, select

from raundo.admin_api.security import hash_password
from raundo.db.session import get_session_factory
from raundo.domain import REVIEW_SUBMITTED
from raundo.models.admin_panel import (
    AdminAccount,
    AdminAuditLog,
    AdminSession,
    SupportMessage,
    SupportTicket,
)
from raundo.models.startup import StartupReview
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.consents import CONSENT_PROFILE_PUBLICATION, ConsentService
from raundo.services.organizations import OrganizationService
from raundo.services.outbox_worker import deliver_pending
from raundo.services.startups import StartupService
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
    """asyncpg pools are loop-bound; each test gets a fresh event loop."""
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _account(factory, *, role: str, login: str | None = None, mfa: bool = False):  # noqa: ANN001
    account = AdminAccount(
        id=uuid.uuid4(),
        login=login or f"suite_{uuid.uuid4().hex[:10]}",
        password_hash=hash_password(PASSWORD),
        role=role,
        must_change_password=False,
        mfa_enabled=mfa,
    )
    async with factory() as session:
        session.add(account)
        await session.commit()
    return account


def _client() -> httpx.AsyncClient:
    from raundo.admin_api.app import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://admin.test")


async def _login(client, login: str, password: str = PASSWORD):  # noqa: ANN001
    response = await client.post("/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    client.headers["x-csrf-token"] = body["csrf"]
    return body


async def _cleanup(factory, *, accounts, users):  # noqa: ANN001
    async with factory() as session:
        for user in users:
            await session.execute(delete(User).where(User.id == _ident(user)))
        for account in accounts:
            await session.execute(
                delete(AdminAccount).where(AdminAccount.id == _ident(account))
            )
        await session.commit()


def _ident(value):  # noqa: ANN001, ANN202
    return getattr(value, "id", value)


@pytest.mark.asyncio
async def test_moderation_support_org_and_outbox_on_postgres(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    async with factory() as session:
        founder = await AccountService(session).bind_telegram_user(
            telegram_id=771_001,
            username="suite_founder",
            first_name="Suite",
            last_name="Founder",
            language_code="ru",
        )
        # Publishing a startup now requires the founder's publication consent.
        consents = ConsentService(session)
        await consents.accept_required(founder.id, source="suite")
        await consents.grant(founder.id, CONSENT_PROFILE_PUBLICATION, source="suite")
        await session.commit()

    startup_id = None
    ticket_id = None
    try:
        async with _client() as client:
            await _login(client, owner.login)

            # --- startup moderation -> audit + outbox -> fake delivery
            async with factory() as session:
                from tests.test_startup_service import _fill_minimal

                svc = StartupService(session)
                startup = await svc.create_draft(founder.id, f"Suite {uuid.uuid4().hex[:6]}")
                await _fill_minimal(session, startup, svc)
                await svc.submit_for_review(startup, founder.id)
                await session.commit()
                startup_id = startup.id
                queue = (
                    await session.execute(
                        select(StartupReview).where(StartupReview.startup_id == startup_id)
                    )
                ).scalars()
                review_id = next(r.id for r in queue if r.state == REVIEW_SUBMITTED)

            response = await client.get("/moderation/queue")
            assert response.status_code == 200
            assert any(item["review_id"] == str(review_id) for item in response.json()["items"])

            response = await client.post(f"/moderation/startups/{review_id}/approve")
            assert response.status_code == 200, response.text

            # Second decision on the same review is refused server-side.
            response = await client.post(f"/moderation/startups/{review_id}/approve")
            assert response.status_code == 409

            async with factory() as session:
                audit = (
                    await session.execute(
                        select(AdminAuditLog).where(
                            AdminAuditLog.resource_id == str(startup_id),
                            AdminAuditLog.action == "approve",
                        )
                    )
                ).scalar_one()
                assert audit.actor_id == owner.id
                event = (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.resource_id == str(startup_id))
                    )
                ).scalar_one()
                assert event.state == "pending"
                sender = FakeTelegram()
                assert await deliver_pending(session, sender) >= 1
                await session.commit()
                assert any("одобрена" in text for _tg, text in sender.sent)
                await session.refresh(event)
                assert event.state == "accepted"

            # --- support: user message -> admin reply -> delivery state
            async with factory() as session:
                from raundo.services.support_tickets import Attachment, create_ticket

                ticket = await create_ticket(
                    session,
                    user_id=founder.id,
                    subject="Suite вопрос",
                    body="Тело обращения",
                    category="premium",
                    attachment=Attachment(
                        kind="photo", file_id="suite-file", name=None, size=2048
                    ),
                )
                await session.commit()
                ticket_id = ticket.id

            listing = await client.get("/support/tickets")
            assert listing.status_code == 200
            assert any(item["id"] == str(ticket_id) for item in listing.json()["items"])

            reply = await client.post(
                f"/support/tickets/{ticket_id}/reply",
                json={"text": "Ответ из веб-панели", "internal": False},
            )
            assert reply.status_code == 200, reply.text
            assert reply.json()["delivery"] == "pending"
            # A staff answer waits for the user's confirmation.
            assert reply.json()["status"] == "awaiting_confirmation"
            assert reply.json()["confirmation_round"] == 1
            assert reply.json()["resolved_at"] is None

            detail = await client.get(f"/support/tickets/{ticket_id}")
            assert detail.status_code == 200
            payload = detail.json()
            assert payload["status"] == "awaiting_confirmation"
            assert payload["resolved_at"] is None
            assert payload["confirmed_at"] is None
            assert "waiting_user" in payload["allowed_statuses"]
            user_message = next(m for m in payload["messages"] if m["kind"] == "user")
            assert user_message["attachment"]["kind"] == "photo"
            staff_message = next(m for m in payload["messages"] if m["kind"] == "staff")
            assert staff_message["delivery"] == "pending"

            # The outbox event for this answer carries the round token.
            async with factory() as session:
                outbox = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.resource_id == str(ticket_id))
                )
                assert outbox is not None
                assert (outbox.safe_payload or {})["confirm_round"] == 1

            # User taps "✅ Да, решён" in Telegram (service-level callback path).
            async with factory() as session:
                from raundo.models.admin_panel import SupportTicket as _Ticket
                from raundo.services.support_tickets import confirm_resolution

                row = await session.get(_Ticket, ticket_id)
                assert confirm_resolution(row, user_id=founder.id, session=session) is True
                await session.commit()
            confirmed = await client.get(f"/support/tickets/{ticket_id}")
            assert confirmed.json()["status"] == "resolved"
            assert confirmed.json()["confirmed_at"] is not None
            assert confirmed.json()["confirmed_by"]
            assert confirmed.json()["resolved_at"] is not None

            # Explicit clarification is a different state, with no buttons.
            async with factory() as session:
                from raundo.services.support_tickets import create_ticket as _create

                clarify = await _create(
                    session, user_id=founder.id, subject="Suite уточнение", body="Нужны детали"
                )
                await session.commit()
                clarify_id = clarify.id
            clarify_reply = await client.post(
                f"/support/tickets/{clarify_id}/reply",
                json={"text": "Уточните стадию", "internal": False, "outcome": "clarify"},
            )
            assert clarify_reply.status_code == 200, clarify_reply.text
            assert clarify_reply.json()["status"] == "waiting_user"
            clarify_detail = await client.get(f"/support/tickets/{clarify_id}")
            assert clarify_detail.json()["status"] == "waiting_user"
            async with factory() as session:
                event = await session.scalar(
                    select(OutboxEvent).where(OutboxEvent.resource_id == str(clarify_id))
                )
                assert event is not None
                assert "confirm_round" not in (event.safe_payload or {})

            # The user's answer reopens the ticket into the staff queue.
            async with factory() as session:
                from raundo.models.admin_panel import SupportTicket as _Ticket2
                from raundo.services.support_tickets import add_user_message

                row = await session.get(_Ticket2, clarify_id)
                await add_user_message(
                    session, ticket=row, user_id=founder.id, body="Стадия MVP"
                )
                await session.commit()
            reopened_by_user = await client.get(f"/support/tickets/{clarify_id}")
            assert reopened_by_user.json()["status"] == "new"

            # "❌ Нет" keeps the ticket active as "needs_help".
            async with factory() as session:
                from raundo.models.admin_panel import SupportTicket as _Ticket3
                from raundo.services.support_tickets import (
                    add_system_message as _system,
                )
                from raundo.services.support_tickets import (
                    reject_resolution as _reject,
                )
                from raundo.services.support_tickets import (
                    staff_reply as _staff_reply,
                )

                row = await session.get(_Ticket3, clarify_id)
                await _staff_reply(
                    session, ticket=row, staff_id=owner.id, body="Повторный ответ"
                )
                assert _reject(row, user_id=founder.id, session=session) is True
                _system(session, ticket=row, body="Пользователь сообщил, что вопрос не решён.")
                await session.commit()
            disputed = await client.get(f"/support/tickets/{clarify_id}")
            assert disputed.json()["status"] == "needs_help"
            assert disputed.json()["disputed_at"] is not None

            note = await client.post(
                f"/support/tickets/{ticket_id}/reply",
                json={"text": "Внутренняя заметка", "internal": True},
            )
            assert note.status_code == 200
            assert note.json()["delivery"] == "not_applicable"

            async with factory() as session:
                sender = FakeTelegram()
                await deliver_pending(session, sender)
                await session.commit()

            detail = await client.get(f"/support/tickets/{ticket_id}")
            staff_message = next(m for m in detail.json()["messages"] if m["kind"] == "staff")
            assert staff_message["delivery"] == "delivered"
            assert "Удалось решить ваш вопрос?" in sender.sent[0][1]

            closed = await client.patch(
                f"/support/tickets/{ticket_id}/status", json={"status": "closed"}
            )
            assert closed.status_code == 200
            assert closed.json()["resolved_by"] == owner.login
            invalid = await client.patch(
                f"/support/tickets/{ticket_id}/status", json={"status": "waiting_user"}
            )
            assert invalid.status_code == 400
            reopened = await client.patch(
                f"/support/tickets/{ticket_id}/status", json={"status": "new"}
            )
            assert reopened.status_code == 200
            assert reopened.json()["status"] == "new"
            assert reopened.json()["resolved_at"] is None

            async with factory() as session:
                actions = (
                    await session.execute(
                        select(AdminAuditLog.action).where(
                            AdminAuditLog.resource_type == "ticket",
                            AdminAuditLog.resource_id.in_([str(ticket_id), str(clarify_id)]),
                        )
                    )
                ).scalars().all()
                assert "ticket_reply" in actions
                assert "ticket_status" in actions
                assert "ticket_user_confirmed" in actions
                assert "ticket_user_disputed" in actions

            # --- organization and representative mandate
            async with factory() as session:
                org, membership = await OrganizationService(session).claim_organization(
                    user_id=founder.id,
                    name=f"Suite Fund {uuid.uuid4().hex[:6]}",
                    participant_type="venture_fund",
                    role="partner",
                    claims_direct_investment=True,
                )
                await session.commit()
                org_id, membership_id = org.id, membership.id

            review = await client.post(
                f"/organizations/{org_id}/review", json={"decision": "approve"}
            )
            assert review.status_code == 200, review.text
            assert review.json()["review_status"] == "approved"

            verified = await client.post(
                f"/organizations/memberships/{membership_id}/review",
                json={"decision": "approve", "mandate": True},
            )
            assert verified.status_code == 200, verified.text
            assert verified.json() == {"ok": True, "status": "approved", "mandate": True}

            revoke = await client.post(
                f"/organizations/memberships/{membership_id}/review",
                json={"decision": "revoke", "reason": "Полномочия прекращены."},
            )
            assert revoke.status_code == 200
            assert revoke.json()["mandate"] is False

            # --- RBAC: moderator cannot read staff or audit
            moderator = await _account(factory, role="moderator")
            async with _client() as mod_client:
                await _login(mod_client, moderator.login)
                assert (await mod_client.get("/staff")).status_code == 403
                assert (await mod_client.get("/audit")).status_code == 403
                assert (await mod_client.get("/support/tickets")).status_code == 403
                assert (await mod_client.get("/organizations")).status_code == 200
                # IDOR-shaped check: CSRF is required for state changing calls
                response = await mod_client.post(
                    f"/support/tickets/{ticket_id}/reply",
                    json={"text": "нельзя", "internal": False},
                    headers={"x-csrf-token": "wrong"},
                )
                assert response.status_code == 403
    finally:
        await _cleanup(factory, accounts=[owner], users=[founder.id])


@pytest.mark.asyncio
async def test_mfa_backup_codes_and_session_revocation_on_postgres(factory) -> None:  # noqa: ANN001
    account = await _account(factory, role="administrator")
    try:
        async with _client() as client:
            await _login(client, account.login)
            setup = await client.post("/auth/mfa/setup")
            assert setup.status_code == 200, setup.text
            secret = setup.json()["secret"]
            assert secret

            bad = await client.post("/auth/mfa/enable", json={"code": "000000"})
            assert bad.status_code == 400

            good = await client.post(
                "/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}
            )
            assert good.status_code == 200, good.text
            codes = good.json()["backup_codes"]
            assert len(codes) == 8

            me = await client.get("/auth/me")
            assert me.json()["mfa_enabled"] is True
            assert me.json()["mfa_pending"] is False

            # Fresh login is gated until TOTP/backup code is supplied.
            async with _client() as second:
                await _login(second, account.login)
                assert (await second.get("/overview")).status_code == 403
                wrong = await second.post("/auth/mfa/verify-login", json={"code": "123456"})
                assert wrong.status_code == 401
                verified = await second.post(
                    "/auth/mfa/verify-login", json={"code": codes[0]}
                )
                assert verified.status_code == 200, verified.text
                assert (await second.get("/overview")).status_code == 200

                # The backup code is single use.
                async with _client() as third:
                    await _login(third, account.login)
                    reused = await third.post(
                        "/auth/mfa/verify-login", json={"code": codes[0]}
                    )
                    assert reused.status_code == 401
                    verified_totp = await third.post(
                        "/auth/mfa/verify-login",
                        json={"code": pyotp.TOTP(secret).now()},
                    )
                    assert verified_totp.status_code == 200, verified_totp.text
                    assert (await client.get("/auth/sessions")).status_code == 401
                    assert (await second.get("/auth/sessions")).status_code == 401
                    sessions = await third.get("/auth/sessions")
                    assert sessions.status_code == 200
                    items = sessions.json()["items"]
                    assert len(items) == 1 and items[0]["current"] is True
                    revoke = await third.post("/auth/sessions/revoke-all")
                    assert revoke.status_code == 200
                    assert revoke.json()["revoked"] == 0

            # TOTP login works after the backup code was consumed.
            async with _client() as fourth:
                await _login(fourth, account.login)
                ok = await fourth.post(
                    "/auth/mfa/verify-login", json={"code": pyotp.TOTP(secret).now()}
                )
                assert ok.status_code == 200, ok.text
    finally:
        async with factory() as session:
            await session.execute(delete(AdminAccount).where(AdminAccount.id == account.id))
            await session.commit()


@pytest.mark.asyncio
async def test_staff_invite_role_change_and_disable_on_postgres(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    created_ids: list[uuid.UUID] = [owner.id]
    try:
        async with _client() as client:
            await _login(client, owner.login)
            login = f"suite_inv_{uuid.uuid4().hex[:8]}"
            invite = await client.post("/staff/invites", json={"login": login, "role": "support"})
            assert invite.status_code == 200, invite.text
            token = invite.json()["invite_token"]

            async with _client() as guest:
                accepted = await guest.post(
                    "/staff/accept", json={"token": token, "password": "staff-password-1"}
                )
                assert accepted.status_code == 200, accepted.text
                # Invite tokens are single use.
                again = await guest.post(
                    "/staff/accept", json={"token": token, "password": "staff-password-1"}
                )
                assert again.status_code == 400

            staff_rows = await client.get("/staff")
            staff = next(i for i in staff_rows.json()["items"] if i["login"] == login)
            created_ids.append(uuid.UUID(staff["id"]))
            assert staff["role"] == "support"
            # The invitee chose this password, so no forced rotation follows.
            assert staff["must_change_password"] is False

            async with _client() as staff_client:
                await staff_client.post(
                    "/auth/login", json={"login": login, "password": "staff-password-1"}
                )
                me = await staff_client.get("/auth/me")
                csrf = me.json()["csrf"]
                staff_client.headers["x-csrf-token"] = csrf
                assert (await staff_client.get("/support/tickets")).status_code == 200
                changed = await staff_client.post(
                    "/auth/password",
                    json={
                        "password": "brand-new-password",
                        "current_password": "staff-password-1",
                    },
                )
                assert changed.status_code == 200
                assert (await staff_client.get("/support/tickets")).status_code == 200

            promoted = await client.post(
                f"/staff/{staff['id']}/role", json={"role": "moderator"}
            )
            assert promoted.status_code == 200
            assert promoted.json()["role"] == "moderator"

            disabled = await client.post(f"/staff/{staff['id']}/disable")
            assert disabled.status_code == 200
            async with _client() as staff_client:
                login_attempt = await staff_client.post(
                    "/auth/login", json={"login": login, "password": "brand-new-password"}
                )
                assert login_attempt.status_code == 401

            enabled = await client.post(f"/staff/{staff['id']}/enable")
            assert enabled.status_code == 200
            async with _client() as staff_client:
                ok = await staff_client.post(
                    "/auth/login", json={"login": login, "password": "brand-new-password"}
                )
                assert ok.status_code == 200

            # Owner protection and self-escalation guard.
            assert (await client.post(f"/staff/{owner.id}/disable")).status_code == 403
            assert (
                await client.post(f"/staff/{owner.id}/role", json={"role": "support"})
            ).status_code == 403
            assert (
                await client.post(
                    f"/staff/{staff['id']}/role", json={"role": "owner"}
                )
            ).status_code == 400
    finally:
        async with factory() as session:
            for account_id in created_ids:
                await session.execute(delete(AdminAccount).where(AdminAccount.id == account_id))
            await session.commit()


@pytest.mark.asyncio
async def test_search_and_user_detail_on_postgres(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=771_009,
            username="suite_search_user",
            first_name="Searchable",
            last_name=None,
            language_code="ru",
        )
        await session.commit()
        user_id = user.id
    try:
        async with _client() as client:
            await _login(client, owner.login)
            found = await client.get("/search", params={"q": "suite_search"})
            assert found.status_code == 200
            items = found.json()["items"]
            assert any(i["type"] == "user" and i["section"] == "users" for i in items)
            detail = await client.get(f"/users/{user_id}")
            assert detail.status_code == 200
            assert detail.json()["telegram_id"] == 771_009

            missing = await client.get(f"/users/{uuid.uuid4()}")
            assert missing.status_code == 404
    finally:
        await _cleanup(factory, accounts=[owner], users=[user_id])


@pytest.mark.asyncio
async def test_audit_log_records_actor_login(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    try:
        async with _client() as client:
            login_body = await _login(client, owner.login)
            assert login_body["login"] == owner.login
            audit = await client.get("/audit")
            assert audit.status_code == 200
            assert isinstance(audit.json()["items"], list)
    finally:
        await _cleanup(factory, accounts=[owner], users=[])


def test_support_messages_keep_attachment_columns(factory=None) -> None:  # noqa: ANN001, ARG001
    """Schema guard: the ORM columns exist on the migrated suite database."""
    columns = set(SupportMessage.__table__.columns.keys())
    assert {"attachment_kind", "attachment_name", "attachment_size", "outbox_event_id"} <= columns
    assert "category" in SupportTicket.__table__.columns
    assert "resolution_comment" in SupportMessage.metadata.tables["complaints"].columns


@pytest.mark.asyncio
async def test_sessions_are_persisted_on_postgres(factory) -> None:  # noqa: ANN001
    account = await _account(factory, role="owner")
    try:
        async with _client() as client:
            await _login(client, account.login)
            async with factory() as session:
                sessions = (
                    await session.execute(
                        select(AdminSession).where(AdminSession.admin_id == account.id)
                    )
                ).scalars()
                assert len(list(sessions)) == 1
    finally:
        await _cleanup(factory, accounts=[account], users=[])


@pytest.mark.asyncio
async def test_password_change_security_and_support_search_scope(factory) -> None:  # noqa: ANN001
    from datetime import datetime, timedelta, timezone

    owner = await _account(factory, role="owner")
    support = await _account(factory, role="support")
    try:
        async with _client() as client:
            await _login(client, owner.login)
            missing = await client.post(
                "/auth/password", json={"password": "another-password-123"}
            )
            assert missing.status_code == 400
            wrong = await client.post(
                "/auth/password",
                json={"password": "another-password-123", "current_password": "wrong-password"},
            )
            assert wrong.status_code == 400
            good = await client.post(
                "/auth/password",
                json={"password": "another-password-123", "current_password": PASSWORD},
            )
            assert good.status_code == 200, good.text

        # Expired sessions must not look active in the panel.
        expired_id = uuid.uuid4()
        async with factory() as session:
            session.add(
                AdminSession(
                    id=expired_id,
                    admin_id=owner.id,
                    token_hash=f"expired-{uuid.uuid4().hex}",
                    csrf_token=uuid.uuid4().hex,
                    expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                )
            )
            await session.commit()

        async with _client() as client:
            await _login(client, owner.login, password="another-password-123")
            sessions = await client.get("/auth/sessions")
            assert sessions.status_code == 200
            items = sessions.json()["items"]
            assert all(item["expires_at"] for item in items)
            assert all(item["id"] != str(expired_id) for item in items)

        async with _client() as support_client:
            await _login(support_client, support.login)
            assert (await support_client.get("/moderation/queue")).status_code == 403
            hits = await support_client.get("/search", params={"q": "E2E"})
            assert hits.status_code == 200
            assert all(hit["section"] != "moderation" for hit in hits.json()["items"])
    finally:
        await _cleanup(factory, accounts=[owner, support], users=[])

@pytest.mark.asyncio
async def test_tax_receipt_registry_records_real_receipt_ids(factory) -> None:  # noqa: ANN001
    """Admin records a real «Мой налог» receipt; no receipt is fabricated."""
    from raundo.config import Settings
    from raundo.services import payments as pay
    from raundo.services.tax_receipts import ensure_pending_receipt

    owner = await _account(factory, role="owner")
    settings = Settings(_env_file=None, PAYMENTS_MODE="demo", PAYMENTS_CARD_ENABLED="true")
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=771_500,
            username="receipt_suite",
            first_name="Receipt",
            last_name="Suite",
            language_code="ru",
        )
        order, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="3d",
            provider=pay.PROVIDER_DEMO,
            settings=settings,
        )
        order.status = pay.ORDER_PAID
        receipt = await ensure_pending_receipt(session, order)
        await session.commit()
        receipt_id = receipt.id
    try:
        async with _client() as client:
            await _login(client, owner.login)

            listed = await client.get("/tax-receipts")
            assert listed.status_code == 200, listed.text
            assert any(item["id"] == str(receipt_id) for item in listed.json()["items"])
            assert listed.json()["counts"].get("pending", 0) >= 1

            empty = await client.post(
                f"/tax-receipts/{receipt_id}/registered",
                json={"fiscal_receipt_id": ""},
            )
            assert empty.status_code == 422

            ok = await client.post(
                f"/tax-receipts/{receipt_id}/registered",
                json={
                    "provider": "my_tax",
                    "fiscal_receipt_id": "2026110500001",
                    "receipt_url": "https://lkfl2.nalog.ru/receipt/example",
                    "sent_to_user": True,
                },
            )
            assert ok.status_code == 200, ok.text
            assert ok.json()["status"] == "registered"

            registered = await client.get("/tax-receipts?status=registered")
            assert registered.status_code == 200
            assert any(item["id"] == str(receipt_id) for item in registered.json()["items"])

        async with factory() as session:
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.kind == "receipt.issued")
            )
            assert event is not None
            assert event.safe_payload["receipt_url"].startswith("https://lkfl2.nalog.ru/")
    finally:
        await _cleanup(factory, accounts=[owner], users=[user])


@pytest.mark.asyncio
async def test_catalog_lists_drafts_and_opens_detail(factory) -> None:  # noqa: ANN001
    owner = await _account(factory, role="owner")
    async with factory() as session:
        founder = await AccountService(session).bind_telegram_user(
            telegram_id=771_600,
            username="catalog_user",
            first_name="Catalog",
            last_name="Draft",
            language_code="ru",
        )
        startup = await StartupService(session).create_draft(founder.id, "Catalog Draft")
        startup.ask_amount_minor = 12_000_000
        startup.ask_currency = "RUB"
        await StartupService(session).add_photo(
            startup, telegram_file_id="catalog-fixture", file_unique_id="catalog-fixture",
        )
        from raundo.models.organization import FundingOrganization

        organizations = [
            FundingOrganization(name="Catalog Fund", normalized_name="catalog fund",
                                participant_type="vc_fund", created_by_user_id=founder.id),
            FundingOrganization(name="Catalog Accelerator", normalized_name="catalog accelerator",
                                participant_type="accelerator", created_by_user_id=founder.id),
        ]
        session.add_all(organizations)
        await session.commit()
        startup_id = startup.id
    try:
        async with _client() as client:
            assert (await client.get("/catalog")).status_code == 401
            await _login(client, owner.login)
            listed = await client.get("/catalog?profile_type=startup&status=draft")
            assert listed.status_code == 200, listed.text
            assert any(item["id"] == str(startup_id) for item in listed.json()["items"])
            item = next(item for item in listed.json()["items"] if item["id"] == str(startup_id))
            assert item["cover_url"].endswith("/photos/1")
            assert "120,000" in item["amount_label"]
            for query in ("Catalog", "@catalog_user", str(founder.telegram_id), str(startup_id)):
                response = await client.get("/catalog", params={"q": query})
                assert response.status_code == 200, response.text
                assert any(row["id"] == str(startup_id) for row in response.json()["items"])
            all_types = await client.get("/catalog", params={"q": "Catalog"})
            assert all_types.status_code == 200, all_types.text
            times = [row["updated_at"] or "" for row in all_types.json()["items"]]
            assert times == sorted(times, reverse=True)
            assert all_types.json()["counts"]["fund"]["total"] >= 1
            assert all_types.json()["counts"]["organization"]["total"] >= 1
            assert all(not key.startswith("_") for row in all_types.json()["items"] for key in row)
            funds = await client.get("/catalog", params={"q": "Catalog", "profile_type": "fund"})
            assert funds.status_code == 200, funds.text
            assert [row["title"] for row in funds.json()["items"]] == ["Catalog Fund"]
            detail = await client.get(f"/catalog/startup/{startup_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["item"]["name"] == "Catalog Draft"
    finally:
        async with factory() as session:
            for organization in organizations:
                await session.delete(await session.get(type(organization), organization.id))
            await session.commit()
        await _cleanup(factory, accounts=[owner], users=[founder])
