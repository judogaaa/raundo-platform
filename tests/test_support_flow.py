"""Support ticket round-trip: user message in DB, staff reply, outbox delivery."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.notifications import NotificationService
from raundo.services.outbox_worker import deliver_pending


class _Capture:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


@pytest.mark.asyncio
async def test_staff_reply_enqueues_and_delivers(session) -> None:
    await ensure_catalog_seeded(session)
    user = await AccountService(session).bind_telegram_user(
        telegram_id=88100,
        username="sup_user",
        first_name="U",
        last_name=None,
        language_code="ru",
    )
    ticket = SupportTicket(
        id=uuid.uuid4(),
        public_number="SUP-TEST-1",
        user_id=user.id,
        subject="Тест",
        status="new",
        priority="normal",
    )
    session.add(ticket)
    session.add(
        SupportMessage(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            author_kind="user",
            body="Вопрос",
        )
    )
    await session.flush()
    await NotificationService(session).enqueue(
        kind="support.reply",
        dedupe_key=f"support_reply:{ticket.id}:1",
        recipient_user_id=user.id,
        resource_type="ticket",
        resource_id=str(ticket.id),
        safe_payload={"text": "Ответ службы поддержки", "number": ticket.public_number},
    )
    row = await session.execute(select(OutboxEvent).where(OutboxEvent.kind == "support.reply").limit(1))
    assert row.scalar_one_or_none() is not None
    cap = _Capture()
    n = await deliver_pending(session, cap)
    assert n == 1
    assert "поддержки" in cap.sent[0][1].lower()
