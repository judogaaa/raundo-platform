"""Unit: actor extraction from Update (the P0 failure mode)."""

from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Chat, Message, Update, User

from raundo.middlewares.actor import extract_actor_user


def test_extract_actor_from_update_message() -> None:
    user = User(id=7, is_bot=False, first_name="A")
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=7, type="private", first_name="A"),
            from_user=user,
            text="/start",
        ),
    )
    assert extract_actor_user(update) is user


def test_extract_actor_from_update_callback() -> None:
    user = User(id=8, is_bot=False, first_name="B")
    message = Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=Chat(id=8, type="private", first_name="B"),
        from_user=user,
        text="x",
    )
    update = Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="c1",
            from_user=user,
            chat_instance="ci",
            data="consent:accept",
            message=message,
        ),
    )
    assert extract_actor_user(update) is user
