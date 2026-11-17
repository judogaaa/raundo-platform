"""Regression tests for Telegram formatting: parse mode, escaping, no raw tags."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.enums import ParseMode

from raundo import ui
from raundo.main import build_bot
from raundo.services.outbox_worker import render_message


def test_bot_defaults_to_html_parse_mode() -> None:
    bot = build_bot("123456789:TEST_TOKEN_FOR_UNIT")
    try:
        assert bot.default.parse_mode == ParseMode.HTML
    finally:
        asyncio.run(bot.session.close())


def test_esc_blocks_html_injection() -> None:
    hostile = '<b>&"\'<script>alert(1)</script>'
    escaped = ui.esc(hostile)
    assert "<b>" not in escaped
    assert "<script>" not in escaped
    assert "&lt;b&gt;" in escaped
    assert "&amp;" in escaped
    assert "&lt;script&gt;" in escaped


def test_builders_escape_values_and_keep_only_our_tags() -> None:
    value = "<i>evil</i>"
    assert ui.b(value) == "<b>&lt;i&gt;evil&lt;/i&gt;</b>"
    assert ui.code(value).startswith("<code>&lt;i&gt;")
    # None and empty values never leak into the message.
    assert ui.esc(None) == "Не указано"
    assert ui.esc("") == "Не указано"


def test_status_and_monetization_are_human() -> None:
    assert ui.status("submitted") == "На проверке"
    assert ui.status("pending_changes") == "Требуются изменения"
    assert ui.status("rejected") == "Отклонено"
    assert ui.monetization("not_yet") == "Пока не монетизируется"
    assert ui.monetization(None) == "Не указано"


def test_limits_formatting() -> None:
    assert ui.limit_line("Стартапы", 2, 3, "Startup PRO") == "Стартапы: 2 из 3 · Startup PRO"
    assert "бесплатный тариф" in ui.limit_line("Стартапы", 1, 1)


@pytest.mark.parametrize(
    "kind",
    [
        "startup.submitted",
        "startup.approved",
        "startup.rejected",
        "startup.changes_requested",
        "investor.submitted",
        "contact.requested",
        "contact.accepted",
        "contact.declined",
        "support.reply",
        "user.restricted",
    ],
)
def test_outbox_messages_have_no_markup(kind: str) -> None:
    """Worker sends plain text; it must never carry HTML tags or raw codes."""
    event = SimpleNamespace(
        kind=kind,
        safe_payload={
            "name": "Проект <b>",
            "text": "Текст",
            "comment": "Комментарий",
            "reason": "Причина",
            "number": "SUP-1",
        },
    )
    message = render_message(event)
    assert message
    # The worker sends plain text (no parse_mode): our markup must never appear.
    for token in ("</b>", "<code>", "</code>", "<i>", "</i>"):
        assert token not in message
