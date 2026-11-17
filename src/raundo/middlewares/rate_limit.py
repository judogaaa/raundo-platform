"""Simple Redis fixed-window rate limit per Telegram user."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from redis.asyncio import Redis

from raundo.middlewares.actor import (
    extract_actor_user,
    extract_callback_query,
    extract_private_message,
    is_private_chat_event,
)

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Works on Update (dispatcher.update) and on Message/CallbackQuery levels.

    Non-private chats are ignored entirely: no counter, no Bot API replies.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        limit: int = 30,
        window_seconds: int = 10,
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not is_private_chat_event(event):
            return await handler(event, data)

        telegram_user = extract_actor_user(event)
        if telegram_user is None:
            return await handler(event, data)

        key = f"raundo:rl:{telegram_user.id}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window)
            if count > self._limit:
                message = extract_private_message(event)
                callback = extract_callback_query(event)
                if callback is not None:
                    await callback.answer("Слишком много запросов", show_alert=False)
                elif message is not None:
                    await message.answer("Слишком много запросов. Подождите несколько секунд.")
                return None
        except Exception:
            # Fail open for availability; log for ops. Do not block the bot if Redis blips.
            logger.exception("Rate limit Redis error; continuing without throttle")
        return await handler(event, data)
