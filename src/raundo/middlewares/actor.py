"""Extract Telegram actor from Update or concrete event objects."""

from __future__ import annotations

from aiogram.types import CallbackQuery, Message, TelegramObject, Update, User


def extract_actor_user(event: TelegramObject) -> User | None:
    """Identity must come from Bot API payload, never from callback_data text."""
    if isinstance(event, Update):
        if event.message is not None and event.message.from_user is not None:
            return event.message.from_user
        if event.edited_message is not None and event.edited_message.from_user is not None:
            return event.edited_message.from_user
        if event.callback_query is not None and event.callback_query.from_user is not None:
            return event.callback_query.from_user
        if event.inline_query is not None and event.inline_query.from_user is not None:
            return event.inline_query.from_user
        if (
            event.chosen_inline_result is not None
            and event.chosen_inline_result.from_user is not None
        ):
            return event.chosen_inline_result.from_user
        return None
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    return None


def extract_private_message(event: TelegramObject) -> Message | None:
    if isinstance(event, Update):
        if event.message is not None:
            return event.message
        if event.callback_query is not None and isinstance(event.callback_query.message, Message):
            return event.callback_query.message
        return None
    if isinstance(event, Message):
        return event
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        return event.message
    return None


def extract_callback_query(event: TelegramObject) -> CallbackQuery | None:
    if isinstance(event, Update):
        return event.callback_query
    if isinstance(event, CallbackQuery):
        return event
    return None


def is_private_chat_event(event: TelegramObject) -> bool:
    """RAUNDO UI and throttling apply only in private chats."""
    message = extract_private_message(event)
    if message is not None:
        return message.chat.type == "private"
    callback = extract_callback_query(event)
    if callback is not None and callback.message is not None:
        chat = getattr(callback.message, "chat", None)
        if chat is not None:
            return chat.type == "private"
    return False
