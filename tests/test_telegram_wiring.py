"""Baseline transport safety: only personal chats and trusted admin IDs."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from raundo.config import Settings
from raundo.handlers import admin_message, router
from raundo.keyboards import (
    MENU_FAVORITES,
    MENU_INVESTOR,
    MENU_PROFILE,
    MENU_RATE,
    MENU_REQUESTS,
    MENU_ROWS,
    MENU_SETTINGS,
    MENU_STARTUP,
    MENU_SUPPORT,
    MENU_TOP,
    main_menu,
)


def _test_message(chat_kind: str) -> Message:
    if chat_kind == "private":
        chat = Chat(id=321, type="private", first_name="Tester")
    else:
        chat = Chat(id=-321, type="supergroup", title="Some group")
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=User(id=321, is_bot=False, first_name="Tester"),
        text="/admin",
    )


@pytest.mark.asyncio
async def test_no_message_handlers_operate_in_groups() -> None:
    allowed, _ = await router.message.check_root_filters(_test_message("supergroup"))
    assert not allowed


@pytest.mark.asyncio
async def test_private_messages_pass_root_router_filter() -> None:
    allowed, _ = await router.message.check_root_filters(_test_message("private"))
    assert allowed


@pytest.mark.asyncio
async def test_admin_panel_denies_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "raundo.routers.admin.get_settings",
        lambda: Settings(_env_file=None, ADMIN_TELEGRAM_IDS="321"),
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=222), answer=AsyncMock())
    await admin_message(message)
    response = message.answer.call_args.args[0]
    assert "запрещ" in response.lower()


@pytest.mark.asyncio
async def test_admin_panel_only_shows_placeholder_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "raundo.routers.admin.get_settings",
        lambda: Settings(_env_file=None, ADMIN_TELEGRAM_IDS="321"),
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=321), answer=AsyncMock())
    await admin_message(message)
    response = message.answer.call_args.args[0]
    assert "RAUNDO Admin" in response and "веб-панел" in response


def test_persistent_reply_keyboard_has_all_sections() -> None:
    labels = [label for row in MENU_ROWS for label in row]
    assert len(MENU_ROWS) == 5
    assert len(set(labels)) == 10
    keyboard = main_menu()
    assert keyboard.is_persistent
    assert len(keyboard.keyboard) == 5


def test_every_router_uses_the_shared_menu_labels() -> None:
    """Regression for the /support bug: the router label had no emoji and the
    menu button silently fell into the unknown-message fallback."""
    from raundo.routers import complaints, feed, investor, premium, profile, startup, support

    assert support.MENU_SUPPORT == MENU_SUPPORT
    assert feed.MENU_RATE == MENU_RATE
    assert feed.MENU_TOP == MENU_TOP
    assert profile.MENU_PROFILE == MENU_PROFILE
    assert profile.MENU_SETTINGS == MENU_SETTINGS
    assert profile.MENU_REQUESTS == MENU_REQUESTS
    assert profile.MENU_FAVORITES == MENU_FAVORITES
    assert startup.MENU_STARTUP == MENU_STARTUP
    assert investor.MENU_INVESTOR == MENU_INVESTOR
    assert complaints is not None and premium is not None
