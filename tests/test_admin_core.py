"""Admin moderation, outbox delivery and internal support notes."""

from __future__ import annotations

import uuid

import pytest

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.domain import REVIEW_APPROVED, REVIEW_SUBMITTED  # noqa: F401
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService
from raundo.services.moderation_decisions import approve_startup, request_startup_changes
from raundo.services.outbox_worker import deliver_pending
from raundo.services.review_guards import ReviewDecisionError
from raundo.services.startups import StartupService
from tests.test_startup_service import _fill_minimal


class _Capture:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


@pytest.mark.asyncio
async def test_approve_startup_enqueues_and_delivers(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88001, username="own", first_name="O", last_name=None, language_code="ru"
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "AdminCo")
    await _fill_minimal(session, startup, svc)
    await ConsentService(session).grant(owner.id, "profile_publication", source="test")
    await svc.submit_for_review(startup, owner.id)
    await session.refresh(startup, attribute_names=["reviews"])
    existing = next(r for r in startup.reviews if r.state == REVIEW_SUBMITTED)
    actor = uuid.uuid4()
    await approve_startup(session, review_id=existing.id, actor_id=actor)
    assert startup.review_state == REVIEW_APPROVED
    capture = _Capture()
    sent = await deliver_pending(session, capture)
    assert sent == 1
    assert "Анкета одобрена" in capture.sent[0][1]


@pytest.mark.asyncio
async def test_stale_revision_cannot_be_approved(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88002, username="own2", first_name="O", last_name=None, language_code="ru"
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "StaleCo")
    await _fill_minimal(session, startup, svc)
    await ConsentService(session).grant(owner.id, "profile_publication", source="test")
    await svc.submit_for_review(startup, owner.id)
    await session.refresh(startup, attribute_names=["reviews"])
    review = next(r for r in startup.reviews if r.state == REVIEW_SUBMITTED)
    startup.current_revision += 1
    await session.flush()
    with pytest.raises(ReviewDecisionError) as exc:
        await approve_startup(session, review_id=review.id, actor_id=uuid.uuid4())
    assert exc.value.code == "stale_revision"


@pytest.mark.asyncio
async def test_internal_note_does_not_enqueue(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88003, username="own3", first_name="O", last_name=None, language_code="ru"
    )
    ticket = SupportTicket(
        id=uuid.uuid4(),
        public_number="SUP-000001",
        user_id=owner.id,
        subject="Вопрос",
        status="new",
        priority="normal",
    )
    session.add(ticket)
    session.add(
        SupportMessage(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            author_kind="note",
            staff_id=None,
            body="внутренняя заметка",
        )
    )
    await session.flush()
    rows = await session.execute(
        OutboxEvent.__table__.select().where(OutboxEvent.kind == "support.reply")
    )
    assert rows.fetchall() == []


@pytest.mark.asyncio
async def test_changes_request_requires_comment(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88004, username="own4", first_name="O", last_name=None, language_code="ru"
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "NeedText")
    await _fill_minimal(session, startup, svc)
    await ConsentService(session).grant(owner.id, "profile_publication", source="test")
    await svc.submit_for_review(startup, owner.id)
    await session.refresh(startup, attribute_names=["reviews"])
    review = next(r for r in startup.reviews if r.state == REVIEW_SUBMITTED)
    with pytest.raises(ReviewDecisionError):
        await request_startup_changes(
            session, review_id=review.id, actor_id=uuid.uuid4(), comment="коротко"
        )

@pytest.mark.asyncio
async def test_second_approval_of_same_review_is_rejected(session) -> None:
    """Two staff members must not approve one revision twice (race-safe guard)."""
    from sqlalchemy import func, select

    from raundo.models.admin_panel import AdminAuditLog

    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88003, username="own3", first_name="O", last_name=None, language_code="ru"
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "RaceCo")
    await _fill_minimal(session, startup, svc)
    await ConsentService(session).grant(owner.id, "profile_publication", source="test")
    await svc.submit_for_review(startup, owner.id)
    await session.refresh(startup, attribute_names=["reviews"])
    review = next(r for r in startup.reviews if r.state == REVIEW_SUBMITTED)
    review_id = review.id
    startup_id = startup.id
    first_actor = uuid.uuid4()
    await approve_startup(session, review_id=review_id, actor_id=first_actor)
    await session.commit()

    # A second staff member holds the same (now stale) review object.
    with pytest.raises(ReviewDecisionError) as exc:
        await approve_startup(session, review_id=review_id, actor_id=uuid.uuid4())
    assert exc.value.code == "not_open"
    await session.rollback()

    audits = await session.scalar(
        select(func.count())
        .select_from(AdminAuditLog)
        .where(
            AdminAuditLog.resource_type == "startup",
            AdminAuditLog.resource_id == str(startup_id),
            AdminAuditLog.action == "approve",
        )
    )
    events = await session.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.resource_id == str(startup_id), OutboxEvent.kind == "startup.approved")
    )
    assert audits == 1
    assert events == 1


@pytest.mark.asyncio
async def test_approval_refused_without_publication_consent(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=88099, username="nopub", first_name="N", last_name=None, language_code="ru"
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "NoConsentCo")
    await _fill_minimal(session, startup, svc)
    await svc.submit_for_review(startup, owner.id)
    await session.refresh(startup, attribute_names=["reviews"])
    review = next(r for r in startup.reviews if r.state == REVIEW_SUBMITTED)

    with pytest.raises(ReviewDecisionError) as exc:
        await approve_startup(session, review_id=review.id, actor_id=uuid.uuid4())
    assert exc.value.code == "consent"

    await session.rollback()