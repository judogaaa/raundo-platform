"""Bot handlers: user confirms or disputes a support resolution (real DB session)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from raundo.models.admin_panel import SupportMessage
from raundo.routers.common import _support_continuation
from raundo.routers.support import (
    SupportFlow,
    on_choose_ticket,
    on_dispute_message,
    on_resolution_callback,
)
from raundo.services.accounts import AccountService
from raundo.services.support_tickets import confirmation_buttons, create_ticket, staff_reply


async def _state() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))


async def _user(session, telegram_id: int):  # noqa: ANN001
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="U",
        last_name=None,
        language_code="ru",
    )


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.caption = None
        self.photo = None
        self.document = None
        self.chat = SimpleNamespace(id=1)
        self.bot = SimpleNamespace(delete_message=AsyncMock())
        self.answer = AsyncMock()
        self.edit_reply_markup = AsyncMock()


def _callback(data: str) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = FakeMessage()
    return cb


async def _awaiting_ticket(session, telegram_id: int = 78001):  # noqa: ANN001
    user = await _user(session, telegram_id)
    ticket = await create_ticket(session, user_id=user.id, subject="Вопрос", body="Текст")
    await staff_reply(session, ticket=ticket, staff_id=uuid.uuid4(), body="Ответ поддержки")
    await session.flush()
    return user, ticket


@pytest.mark.asyncio
async def test_user_confirms_resolution(session) -> None:
    user, ticket = await _awaiting_ticket(session)
    state = await _state()
    data = confirmation_buttons(ticket)[0][1]
    cb = _callback(data)  # type: ignore[arg-type]
    await on_resolution_callback(cb, session, user, state)

    assert ticket.status == "resolved"
    assert ticket.confirmed_by_user_id == user.id
    assert ticket.confirmed_at is not None
    cb.answer.assert_awaited()
    cb.message.edit_reply_markup.assert_awaited_with(reply_markup=None)
    texts = [call.args[0] for call in cb.message.answer.await_args_list]
    assert any("Обращение завершено" in text for text in texts)
    systems = (
        await session.execute(
            select(SupportMessage).where(
                SupportMessage.ticket_id == ticket.id,
                SupportMessage.author_kind == "system",
            )
        )
    ).scalars().all()
    assert systems and "подтвердил" in systems[0].body


@pytest.mark.asyncio
async def test_user_disputes_and_describes_problem(session) -> None:
    user, ticket = await _awaiting_ticket(session, 78002)
    state = await _state()
    data = confirmation_buttons(ticket)[1][1]
    cb = _callback(data)  # type: ignore[arg-type]
    await on_resolution_callback(cb, session, user, state)

    assert ticket.status == "needs_help"
    assert ticket.disputed_at is not None
    assert await state.get_state() == SupportFlow.dispute.state
    texts = [call.args[0] for call in cb.message.answer.await_args_list]
    assert any("что осталось нерешённым" in text for text in texts)

    msg = FakeMessage("Не помогло, проблема осталась")
    await on_dispute_message(msg, session, user, state)  # type: ignore[arg-type]
    assert ticket.status == "new"
    assert await state.get_state() is None
    rows = (
        await session.execute(
            select(SupportMessage).where(
                SupportMessage.ticket_id == ticket.id,
                SupportMessage.author_kind == "user",
                SupportMessage.body == "Не помогло, проблема осталась",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_foreign_user_cannot_finish_ticket(session) -> None:
    _owner, ticket = await _awaiting_ticket(session, 78003)
    stranger = await _user(session, 78004)
    state = await _state()
    cb = _callback(confirmation_buttons(ticket)[0][1])  # type: ignore[arg-type]
    await on_resolution_callback(cb, session, stranger, state)
    assert ticket.status == "awaiting_confirmation"
    cb.answer.assert_awaited()
    assert cb.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_stale_button_is_rejected_without_changes(session) -> None:
    user, ticket = await _awaiting_ticket(session, 78005)
    state = await _state()
    stale = f"sv:yes:{ticket.id}:99"
    cb = _callback(stale)  # type: ignore[arg-type]
    await on_resolution_callback(cb, session, user, state)
    assert ticket.status == "awaiting_confirmation"
    cb.message.edit_reply_markup.assert_awaited_with(reply_markup=None)
    assert cb.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_repeated_yes_is_idempotent(session) -> None:
    user, ticket = await _awaiting_ticket(session, 78006)
    state = await _state()
    data = confirmation_buttons(ticket)[0][1]
    await on_resolution_callback(_callback(data), session, user, state)  # type: ignore[arg-type]
    before = ticket.confirmed_at
    cb = _callback(data)  # type: ignore[arg-type]
    await on_resolution_callback(cb, session, user, state)
    assert ticket.confirmed_at == before
    assert cb.message.answer.await_count == 0
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_unsolicited_message_attaches_to_single_open_ticket(session) -> None:
    user, ticket = await _awaiting_ticket(session, 78007)
    state = await _state()
    message = FakeMessage("У меня ещё вопрос")
    handled = await _support_continuation(message, session, user, state)  # type: ignore[arg-type]
    assert handled is True
    assert ticket.status == "new"
    texts = [call.args[0] for call in message.answer.await_args_list]
    assert any(ticket.public_number in text for text in texts)
    assert "К какому обращению" not in texts[0]


@pytest.mark.asyncio
async def test_several_open_tickets_require_explicit_choice(session) -> None:
    user = await _user(session, 78008)
    first = await create_ticket(session, user_id=user.id, subject="Первый", body="Текст 1")
    second = await create_ticket(session, user_id=user.id, subject="Второй", body="Текст 2")
    await staff_reply(session, ticket=first, staff_id=uuid.uuid4(), body="Ответ 1")
    await staff_reply(session, ticket=second, staff_id=uuid.uuid4(), body="Ответ 2")
    await session.flush()
    state = await _state()
    message = FakeMessage("Продолжение по одному из обращений")
    handled = await _support_continuation(message, session, user, state)  # type: ignore[arg-type]
    assert handled is True
    assert await state.get_state() == SupportFlow.choose_ticket.state
    keyboard = message.answer.await_args.kwargs["reply_markup"]
    flat = [button for row in keyboard.inline_keyboard for button in row]
    assert len(flat) == 2
    assert all(button.callback_data.startswith("sv:pick:") for button in flat)
    assert all(len(button.callback_data.encode()) <= 64 for button in flat)

    target = flat[0].callback_data.split(":")[-1]
    cb = _callback(f"sv:pick:{target}")  # type: ignore[arg-type]
    await on_choose_ticket(cb, session, user, state)
    await session.refresh(first)
    await session.refresh(second)
    changed = [t for t in (first, second) if t.status == "new"]
    assert len(changed) == 1
    attached = (
        await session.execute(
            select(SupportMessage).where(
                SupportMessage.ticket_id == changed[0].id,
                SupportMessage.body == "Продолжение по одному из обращений",
            )
        )
    ).scalars().all()
    assert len(attached) == 1
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_dispute_accepts_photo_attachment(session) -> None:
    user, ticket = await _awaiting_ticket(session, 78009)
    state = await _state()
    await on_resolution_callback(  # type: ignore[arg-type]
        _callback(confirmation_buttons(ticket)[1][1]), session, user, state
    )
    message = FakeMessage(None)
    message.caption = "Скриншот ошибки"
    message.photo = [
        SimpleNamespace(file_id="photo-file-1", file_unique_id="uniq-1", file_size=2048)
    ]
    await on_dispute_message(message, session, user, state)  # type: ignore[arg-type]
    assert ticket.status == "new"
    row = (
        await session.execute(
            select(SupportMessage).where(
                SupportMessage.ticket_id == ticket.id,
                SupportMessage.author_kind == "user",
                SupportMessage.attachment_file_id == "photo-file-1",
            )
        )
    ).scalars().all()
    assert len(row) == 1
    assert row[0].attachment_kind == "photo"
    assert row[0].body == "Скриншот ошибки"
