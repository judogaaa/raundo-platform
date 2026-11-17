"""Block product handlers when a real access restriction is active.

The restriction is created by an administrator in RAUNDO Admin and mirrored in the
`users.status` column. This middleware enforces it for every update, so the web console
cannot claim a block that the bot does not honour. Support stays reachable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.admin_panel import UserRestriction

# Commands a restricted account may still use (support, exit, legal, restart).
ALLOWED_COMMANDS = {
    "/start",
    "/support",
    "/complaint",
    "/help",
    "/cancel",
    "/terms",
    "/privacy",
    "/paysupport",
}

NOTICE = (
    "Доступ к RAUNDO ограничен администрацией.\n\n"
    "Если это ошибка, напишите в поддержку: /support"
)


def _command_of(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return None
    return text.split()[0].split("@")[0].lower()


class RestrictionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("db_user")
        session: AsyncSession | None = data.get("session")
        if user is None or session is None or getattr(user, "status", None) != "restricted":
            return await handler(event, data)

        active = await session.scalar(
            select(UserRestriction.id).where(
                UserRestriction.user_id == user.id,
                UserRestriction.active.is_(True),
            )
        )
        if active is None:
            # Status drifted without an active restriction - let the flow continue so a
            # stale flag never locks a person out of the product.
            return await handler(event, data)

        if isinstance(event, Message):
            if _command_of(event) in ALLOWED_COMMANDS:
                return await handler(event, data)
            await event.answer(NOTICE)
            return None
        if isinstance(event, CallbackQuery):
            await event.answer("Доступ ограничен. Напишите /support", show_alert=True)
            return None
        return None


__all__ = ["ALLOWED_COMMANDS", "NOTICE", "RestrictionMiddleware"]
