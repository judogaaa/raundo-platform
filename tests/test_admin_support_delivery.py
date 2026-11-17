"""Support ticket lifecycle: staff answer → user confirmation in Telegram."""

from __future__ import annotations

import uuid

import pytest

from raundo.models.admin_panel import SupportMessage
from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.outbox_worker import (
    TelegramBlockedError,
    TelegramRateLimitError,
    deliver_pending,
    support_buttons,
)
from raundo.services.support_tickets import (
    Attachment,
    SupportError,
    add_user_message,
    confirm_resolution,
    confirmation_buttons,
    create_ticket,
    delivery_state,
    reject_resolution,
    set_ticket_status,
    staff_reply,
)


class _Capture:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.buttons: list[tuple[int, str, tuple[tuple[str, str], ...]]] = []
        self.fail = fail

    async def send_text(self, telegram_id: int, text: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((telegram_id, text))

    async def send_with_buttons(
        self, telegram_id: int, text: str, buttons: tuple[tuple[str, str], ...]
    ) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((telegram_id, text))
        self.buttons.append((telegram_id, text, buttons))


async def _user(session, telegram_id: int = 77001):  # noqa: ANN001
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="U",
        last_name=None,
        language_code="ru",
    )


@pytest.mark.asyncio
async def test_create_ticket_stores_attachment_metadata(session) -> None:
    user = await _user(session)
    ticket = await create_ticket(
        session,
        user_id=user.id,
        subject="Не приходит код",
        body="Помогите со входом",
        category="account",
        attachment=Attachment(kind="document", file_id="file-1", name="log.txt", size=1024),
    )
    await session.flush()
    assert ticket.category == "account"
    row = (
        await session.execute(
            SupportMessage.__table__.select().where(SupportMessage.ticket_id == ticket.id)
        )
    ).first()
    assert row is not None
    assert row.attachment_kind == "document"
    assert row.attachment_name == "log.txt"
    assert row.attachment_size == 1024
    assert "[вложение:" not in row.body


@pytest.mark.asyncio
async def test_attachment_over_limit_rejected(session) -> None:
    user = await _user(session, 77002)
    with pytest.raises(SupportError) as exc:
        await create_ticket(
            session,
            user_id=user.id,
            subject="Большой файл",
            body="текст",
            attachment=Attachment(kind="document", file_id="f", name="big.zip", size=6 * 1024 * 1024),
        )
    assert exc.value.code == "attachment_size"


@pytest.mark.asyncio
async def test_answer_waits_for_user_confirmation(session) -> None:
    user = await _user(session, 77003)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст вопроса")
    staff_id = uuid.uuid4()
    message, event = await staff_reply(
        session,
        ticket=ticket,
        staff_id=staff_id,
        body="Ответ поддержки",
    )
    await session.flush()
    assert event is not None
    assert message.outbox_event_id == event.id
    assert delivery_state(message, event) == "pending"
    # The staff answer does NOT finish the ticket: the user confirms it.
    assert ticket.status == "awaiting_confirmation"
    assert ticket.confirmation_round == 1
    assert ticket.resolved_at is None
    assert ticket.confirmed_at is None
    assert event.safe_payload["confirm_round"] == 1

    buttons = support_buttons(event)
    assert buttons is not None
    labels = [label for label, _ in buttons]
    assert labels == ["✅ Да, решён", "❌ Нет"]
    assert all(len(data.encode()) <= 64 for _, data in buttons)
    assert str(ticket.id) in buttons[0][1]

    capture = _Capture()
    sent = await deliver_pending(session, capture)
    assert sent == 1
    assert "Ответ поддержки" in capture.sent[0][1]
    assert "Удалось решить ваш вопрос?" in capture.sent[0][1]
    assert capture.buttons[0][2] == buttons
    await session.refresh(event)
    assert delivery_state(message, event) == "delivered"


@pytest.mark.asyncio
async def test_internal_note_is_not_delivered(session) -> None:
    user = await _user(session, 77004)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст вопроса")
    note, event = await staff_reply(
        session, ticket=ticket, staff_id=uuid.uuid4(), body="внутренняя заметка", internal=True
    )
    await session.flush()
    assert event is None
    assert note.author_kind == "note"
    rows = await session.execute(select_outbox())
    assert rows.fetchall() == []
    assert ticket.status == "in_progress"


def select_outbox():  # noqa: ANN201
    return OutboxEvent.__table__.select().where(OutboxEvent.kind == "support.reply")


@pytest.mark.asyncio
async def test_clarification_has_no_confirmation_buttons(session) -> None:
    user = await _user(session, 77005)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст вопроса")
    _message, event = await staff_reply(
        session,
        ticket=ticket,
        staff_id=uuid.uuid4(),
        body="Уточните, пожалуйста, стадию",
        outcome="clarify",
    )
    assert ticket.status == "waiting_user"
    assert ticket.confirmation_round == 1
    assert event is not None
    assert "confirm_round" not in (event.safe_payload or {})
    assert support_buttons(event) is None


@pytest.mark.asyncio
async def test_closed_ticket_rejects_staff_reply(session) -> None:
    user = await _user(session, 77011)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    ticket.status = "closed"
    with pytest.raises(SupportError) as exc:
        await staff_reply(
            session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ"
        )
    assert exc.value.code == "closed"


@pytest.mark.asyncio
async def test_duplicate_answer_is_idempotent(session) -> None:
    user = await _user(session, 77012)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    first, first_event = await staff_reply(
        session, ticket=ticket, staff_id=uuid.uuid4(), body="Один ответ"
    )
    second, second_event = await staff_reply(
        session, ticket=ticket, staff_id=uuid.uuid4(), body="Один ответ"
    )
    assert second.id == first.id
    assert second_event is not None and first_event is not None
    assert second_event.id == first_event.id
    # No second round, no second event, no duplicate message.
    assert ticket.confirmation_round == 1
    rows = await session.execute(
        SupportMessage.__table__.select().where(SupportMessage.ticket_id == ticket.id)
    )
    assert len([row for row in rows.fetchall() if row.author_kind == "staff"]) == 1


@pytest.mark.asyncio
async def test_user_confirmation_finishes_ticket_idempotently(session) -> None:
    user = await _user(session, 77013)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ")
    assert confirm_resolution(ticket, user_id=user.id) is True
    assert ticket.status == "resolved"
    assert ticket.confirmed_at is not None
    assert ticket.confirmed_by_user_id == user.id
    assert ticket.resolved_at is not None
    assert ticket.resolved_by_id is None
    # Repeated tap is a no-op, not an error and not a second completion.
    assert confirm_resolution(ticket, user_id=user.id) is False


@pytest.mark.asyncio
async def test_foreign_user_cannot_confirm_or_dispute(session) -> None:
    owner = await _user(session, 77014)
    stranger = await _user(session, 77015)
    ticket = await create_ticket(session, user_id=owner.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ")
    with pytest.raises(SupportError) as exc:
        confirm_resolution(ticket, user_id=stranger.id)
    assert exc.value.code == "forbidden"
    with pytest.raises(SupportError) as exc2:
        reject_resolution(ticket, user_id=stranger.id)
    assert exc2.value.code == "forbidden"
    assert ticket.status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_user_dispute_moves_to_needs_help_and_message_reopens(session) -> None:
    user = await _user(session, 77016)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ")
    assert reject_resolution(ticket, user_id=user.id) is True
    assert ticket.status == "needs_help"
    assert ticket.disputed_at is not None
    assert ticket.confirmed_at is None
    assert reject_resolution(ticket, user_id=user.id) is False

    await add_user_message(
        session, ticket=ticket, user_id=user.id, body="Не помогло, проблема осталась"
    )
    assert ticket.status == "new"
    assert ticket.resolved_at is None
    assert ticket.confirmed_at is None


@pytest.mark.asyncio
async def test_stale_round_and_status_are_rejected(session) -> None:
    user = await _user(session, 77017)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Первый ответ")
    first_round = ticket.confirmation_round
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Второй ответ")
    assert ticket.confirmation_round == first_round + 1
    # The first answer's buttons carry round 1; the ticket is already on round 2.
    assert confirmation_buttons(ticket)[0][1].endswith(f":{first_round + 1}")
    # Confirming completes the current round and repeated confirmation is a no-op.
    assert confirm_resolution(ticket, user_id=user.id) is True
    assert confirm_resolution(ticket, user_id=user.id) is False


@pytest.mark.asyncio
async def test_manual_transitions_guard_and_clear_confirmation(session) -> None:
    user = await _user(session, 77018)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    actor = uuid.uuid4()
    set_ticket_status(ticket, "resolved", actor_id=actor)
    assert ticket.status == "resolved"
    assert ticket.resolved_by_id == actor
    assert ticket.confirmed_at is None
    with pytest.raises(SupportError) as same:
        set_ticket_status(ticket, "resolved", actor_id=actor)
    assert same.value.code == "same_status"
    set_ticket_status(ticket, "in_progress", actor_id=actor)
    assert ticket.resolved_at is None and ticket.resolved_by_id is None
    set_ticket_status(ticket, "closed", actor_id=actor)
    with pytest.raises(SupportError) as bad:
        set_ticket_status(ticket, "waiting_user", actor_id=actor)
    assert bad.value.code == "bad_transition"


@pytest.mark.asyncio
async def test_user_cannot_touch_foreign_ticket(session) -> None:
    owner = await _user(session, 77006)
    stranger = await _user(session, 77007)
    ticket = await create_ticket(session, user_id=owner.id, subject="Вопрос", body="Текст вопроса")
    with pytest.raises(SupportError) as exc:
        await add_user_message(session, ticket=ticket, user_id=stranger.id, body="чужое")
    assert exc.value.code == "forbidden"


@pytest.mark.asyncio
async def test_closed_ticket_rejects_user_message(session) -> None:
    user = await _user(session, 77008)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст вопроса")
    ticket.status = "closed"
    with pytest.raises(SupportError) as exc:
        await add_user_message(session, ticket=ticket, user_id=user.id, body="ещё вопрос")
    assert exc.value.code == "closed"


@pytest.mark.asyncio
async def test_user_reply_reopens_ticket_and_keeps_category(session) -> None:
    user = await _user(session, 77009)
    ticket = await create_ticket(
        session, user_id=user.id, subject="Вопрос", body="Текст", category="premium"
    )
    ticket.status = "waiting_user"
    await add_user_message(session, ticket=ticket, user_id=user.id, body="Дополнение")
    assert ticket.status == "new"
    assert ticket.category == "premium"


@pytest.mark.asyncio
async def test_delivery_failure_marks_retry_then_failed(session) -> None:
    user = await _user(session, 77010)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    _message, event = await staff_reply(
        session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ"
    )
    assert event is not None
    sent = await deliver_pending(session, _Capture(fail=TelegramRateLimitError(1)))
    assert sent == 0
    await session.refresh(event)
    assert event.state == "retry_scheduled"

    blocked_ticket = await create_ticket(
        session, user_id=user.id, subject="Второй", body="Текст"
    )
    _m2, blocked_event = await staff_reply(
        session, ticket=blocked_ticket, staff_id=uuid.uuid4(), body="Ответ"
    )
    assert blocked_event is not None
    await deliver_pending(session, _Capture(fail=TelegramBlockedError()))
    await session.refresh(blocked_event)
    assert blocked_event.state == "failed"


@pytest.mark.asyncio
async def test_confirmation_buttons_match_ticket_and_round(session) -> None:
    user = await _user(session, 77019)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ")
    buttons = confirmation_buttons(ticket)
    assert buttons[0][1] == f"sv:yes:{ticket.id}:1"
    assert buttons[1][1] == f"sv:no:{ticket.id}:1"
    assert all(len(data.encode()) <= 64 for _, data in buttons)


@pytest.mark.asyncio
async def test_long_answer_is_split_and_buttons_stay_on_last_part(session) -> None:
    user = await _user(session, 77020)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    body = "Очень длинный ответ. " * 250
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body=body)
    capture = _Capture()
    await deliver_pending(session, capture)
    # The answer exceeds one Telegram message; the question and buttons close it.
    assert len(capture.sent) >= 2
    assert capture.buttons and len(capture.buttons) == 1
    assert "Удалось решить ваш вопрос?" in capture.buttons[0][1]
    assert capture.buttons[0][1] == capture.sent[-1][1]


def test_other_notifications_have_no_buttons() -> None:
    from types import SimpleNamespace

    event = SimpleNamespace(
        kind="startup.approved",
        resource_id="abc",
        safe_payload={"name": "Проект", "confirm_round": 1},
    )
    assert support_buttons(event) is None
