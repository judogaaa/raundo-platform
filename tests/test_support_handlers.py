"""Aiogram /support handler integration with in-memory DB session."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.user import User
from raundo.routers.support import on_body, on_category, on_subject, open_support


def _message(text: str, user_id: int = 700001) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.document = None
    msg.photo = None
    return msg


async def _state() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))


@pytest.mark.asyncio
async def test_support_flow_creates_ticket(session) -> None:
    db_user = User(
        id=uuid.uuid4(),
        telegram_id=700001,
        username="tg_user",
        first_name="U",
        status="active",
    )
    session.add(db_user)
    await session.flush()
    state = await _state()
    await open_support(_message("/support"), state)
    await on_category(_message("1"), state, session, db_user)
    await on_subject(_message("Тема тестового обращения"), state)
    await on_body(_message("Текст обращения достаточной длины"), session, db_user, state)
    row = await session.execute(select(SupportTicket).where(SupportTicket.user_id == db_user.id))
    ticket = row.scalar_one()
    assert ticket.public_number.startswith("SUP-")
    msgs = await session.execute(select(SupportMessage).where(SupportMessage.ticket_id == ticket.id))
    assert msgs.scalars().first() is not None
