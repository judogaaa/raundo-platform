"""Final Bot API arguments for outbox plain text and HTML messages."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import SendMessage

from raundo import ui
from raundo.services.outbox_worker import render_message

_spec = importlib.util.spec_from_file_location(
    "run_outbox_worker",
    Path(__file__).resolve().parents[1] / "scripts" / "run-outbox-worker.py",
)
_worker = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_worker)
AiogramSender = _worker.AiogramSender


class FakeSession(BaseSession):
    """Uses aiogram's own prepare_value, so we see the real wire-level parse_mode."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list[SendMessage] = []
        self.parse_modes: list[Any] = []

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None):  # noqa: ANN201
        self.methods.append(method)
        # Same default-resolution path as the real AiohttpSession.
        self.parse_modes.append(self.prepare_value(method.parse_mode, bot=bot, files={}))
        return {
            "ok": True,
            "result": {
                "message_id": 1,
                "date": 1_700_000_000,
                "chat": {"id": method.chat_id, "type": "private"},
                "text": method.text,
            },
        }

    async def stream_content(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        yield b""

    async def close(self) -> None:
        return None


def _bot(session: FakeSession, *, parse_mode: ParseMode | None) -> Bot:
    return Bot(
        token="123456789:TEST_TOKEN_FOR_UNIT",
        session=session,
        default=DefaultBotProperties(parse_mode=parse_mode),
    )


@pytest.mark.asyncio
async def test_outbox_plain_text_overrides_global_html() -> None:
    session = FakeSession()
    # Deliberately create a bot with a global HTML default: the worker must still
    # send notifications as plain text, otherwise '<', '>' and '&' from user
    # comments would break entity parsing.
    bot = _bot(session, parse_mode=ParseMode.HTML)
    text = 'Комментарий: 5 < 7 & "кавычки" <b>не тег</b>'
    try:
        await AiogramSender(bot).send_text(42, text)
    finally:
        await bot.session.close()
    method = session.methods[-1]
    assert session.parse_modes[-1] is None
    assert method.text == text


@pytest.mark.asyncio
async def test_html_message_keeps_escaped_entities() -> None:
    session = FakeSession()
    bot = _bot(session, parse_mode=ParseMode.HTML)
    hostile = '<b>Взлом</b> & <script>'
    text = f"{ui.b(hostile)}\n{ui.status('pending_changes')}"
    try:
        await bot.send_message(42, text)
    finally:
        await bot.session.close()
    method = session.methods[-1]
    assert session.parse_modes[-1] == ParseMode.HTML
    assert "&lt;b&gt;Взлом&lt;/b&gt;" in method.text
    assert "&amp;" in method.text
    assert "&lt;script&gt;" in method.text
    assert method.text.startswith("<b>")  # our own formatting tag


@pytest.mark.asyncio
async def test_plain_notification_builder_has_no_markup() -> None:
    session = FakeSession()
    bot = _bot(session, parse_mode=None)
    event = type(
        "Event",
        (),
        {
            "kind": "support.reply",
            "safe_payload": {
                "text": "Если 5 < 7 & 7 > 5, всё ок <b>",
                "name": "Проект <i>",
                "comment": "Комментарий & <code>",
                "reason": "Причина < >",
                "number": "SUP-1",
            },
        },
    )()
    message = render_message(event)
    try:
        await AiogramSender(bot).send_text(42, message)
    finally:
        await bot.session.close()
    method = session.methods[-1]
    assert session.parse_modes[-1] is None
    for token in ("</b>", "<code>", "</code>", "<i>", "</i>"):
        assert token not in method.text
