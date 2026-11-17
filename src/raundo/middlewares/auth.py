"""Bind Telegram actor to internal user row. Identity always from Bot API update."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.middlewares.actor import extract_actor_user, is_private_chat_event
from raundo.services.accounts import AccountService


class AuthMiddleware(BaseMiddleware):
    """Works on Update (dispatcher.update) and on Message/CallbackQuery levels."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not is_private_chat_event(event):
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        telegram_user = extract_actor_user(event)

        if session is not None and telegram_user is not None and not telegram_user.is_bot:
            accounts = AccountService(session)
            db_user = await accounts.bind_telegram_user(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
                language_code=telegram_user.language_code,
            )
            data["db_user"] = db_user
        return await handler(event, data)
