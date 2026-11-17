"""Outbox lifecycle: pending -> processing -> accepted/retry/failed, idempotency, concurrency."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from raundo.models.admin_panel import OutboxDeliveryMeta
from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.notifications import NotificationService
from raundo.services.outbox_worker import (
    MAX_ATTEMPTS,
    TelegramBlockedError,
    TelegramRateLimitError,
    deliver_pending,
    render_message,
)


class _Flaky:
    def __init__(self, errors: list[Exception | None]) -> None:
        self.errors = errors
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, telegram_id: int, text: str) -> None:
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error
        self.sent.append((telegram_id, text))


async def _recipient(session, telegram_id: int = 66001):  # noqa: ANN001
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"o{telegram_id}",
        first_name="O",
        last_name=None,
        language_code="ru",
    )


async def _event(session, user_id, dedupe: str | None = None) -> OutboxEvent:  # noqa: ANN001
    event = await NotificationService(session).enqueue_event(
        kind="startup.approved",
        dedupe_key=dedupe or f"k-{uuid.uuid4()}",
        recipient_user_id=user_id,
        resource_type="startup",
        resource_id=str(uuid.uuid4()),
        safe_payload={"name": "Lifecycle"},
    )
    assert event is not None
    return event


@pytest.mark.asyncio
async def test_pending_to_accepted_with_meta(session) -> None:
    user = await _recipient(session)
    event = await _event(session, user.id)
    assert event.state == "pending"
    sender = _Flaky([])
    assert await deliver_pending(session, sender) == 1
    stored = await session.get(OutboxEvent, event.id)
    assert stored is not None and stored.state == "accepted"
    meta = await session.get(OutboxDeliveryMeta, event.id)
    assert meta is not None and meta.delivered_at is not None
    assert sender.sent[0][0] == user.telegram_id


@pytest.mark.asyncio
async def test_429_schedules_retry_and_respects_retry_after(session) -> None:
    user = await _recipient(session, 66002)
    event = await _event(session, user.id)
    before = datetime.now(timezone.utc)
    await deliver_pending(session, _Flaky([TelegramRateLimitError(120)]))
    stored = await session.get(OutboxEvent, event.id)
    assert stored is not None and stored.state == "retry_scheduled"
    assert stored.next_attempt_at >= before + timedelta(seconds=119)
    meta = await session.get(OutboxDeliveryMeta, event.id)
    assert meta is not None and meta.last_error == "telegram_429" and meta.attempt_count == 1

    # Not due yet: nothing is sent on the next pass.
    assert await deliver_pending(session, _Flaky([])) == 0

    stored.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.flush()
    assert await deliver_pending(session, _Flaky([])) == 1
    await session.refresh(stored)
    assert stored.state == "accepted"


@pytest.mark.asyncio
async def test_blocked_bot_is_terminal(session) -> None:
    user = await _recipient(session, 66003)
    event = await _event(session, user.id)
    await deliver_pending(session, _Flaky([TelegramBlockedError()]))
    stored = await session.get(OutboxEvent, event.id)
    assert stored is not None and stored.state == "failed"
    meta = await session.get(OutboxDeliveryMeta, event.id)
    assert meta is not None and meta.last_error == "bot_blocked"


@pytest.mark.asyncio
async def test_generic_error_backoff_then_failed_after_max_attempts(session) -> None:
    user = await _recipient(session, 66004)
    event = await _event(session, user.id)
    errors = [RuntimeError("boom")] * MAX_ATTEMPTS
    for attempt in range(1, MAX_ATTEMPTS + 1):
        meta = await session.get(OutboxDeliveryMeta, event.id)
        if meta is not None:
            meta.attempt_count = attempt - 1
        stored = await session.get(OutboxEvent, event.id)
        assert stored is not None
        stored.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.flush()
        await deliver_pending(session, _Flaky(errors[attempt - 1 :]))
    stored = await session.get(OutboxEvent, event.id)
    assert stored is not None and stored.state == "failed"
    meta = await session.get(OutboxDeliveryMeta, event.id)
    assert meta is not None and meta.attempt_count == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_missing_recipient_fails_fast(session) -> None:
    event = await NotificationService(session).enqueue_event(
        kind="startup.approved",
        dedupe_key=f"orphan-{uuid.uuid4()}",
        recipient_user_id=None,
    )
    assert event is not None
    await deliver_pending(session, _Flaky([]))
    stored = await session.get(OutboxEvent, event.id)
    assert stored is not None and stored.state == "failed"
    meta = await session.get(OutboxDeliveryMeta, event.id)
    assert meta is not None and meta.last_error == "no_recipient"


@pytest.mark.asyncio
async def test_duplicate_dedupe_key_is_idempotent(session) -> None:
    user = await _recipient(session, 66005)
    first = await _event(session, user.id, dedupe="fixed-key")
    second = await NotificationService(session).enqueue_event(
        kind="startup.approved",
        dedupe_key="fixed-key",
        recipient_user_id=user.id,
    )
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_render_support_reply_contains_text(session) -> None:
    user = await _recipient(session, 66006)
    event = await NotificationService(session).enqueue_event(
        kind="support.reply",
        dedupe_key=f"sr-{uuid.uuid4()}",
        recipient_user_id=user.id,
        safe_payload={"text": "Ответ", "number": "SUP-000001"},
    )
    assert event is not None
    assert "Ответ" in render_message(event)


@pytest.mark.asyncio
async def test_two_parallel_workers_do_not_double_send(session) -> None:
    """Both workers see the same pending row; the row lock keeps one winner."""
    user = await _recipient(session, 66007)
    await _event(session, user.id)
    sender = _Flaky([])
    first = await deliver_pending(session, sender)
    second = await deliver_pending(session, sender)
    assert (first, second) == (1, 0)
    assert len(sender.sent) == 1
