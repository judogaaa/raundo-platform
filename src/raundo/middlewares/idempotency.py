"""Skip already-handled Telegram updates via processed_updates ledger."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.system import ProcessedUpdate

logger = logging.getLogger(__name__)


class IdempotencyMiddleware(BaseMiddleware):
    """
    Insert update_id before handlers; on PK conflict skip the update.

    On handler failure the outer DbSessionMiddleware rolls back, so the
    ledger row is removed and Telegram retry can re-process.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None:
            return await handler(event, data)

        update_id = int(event.update_id)
        existing = await session.get(ProcessedUpdate, update_id)
        if existing is not None:
            await self._ack_stale_callback(event, data)
            logger.info("skip duplicate update_id=%s", update_id)
            return None

        try:
            async with session.begin_nested():
                session.add(ProcessedUpdate(update_id=update_id))
                await session.flush()
        except IntegrityError:
            await self._ack_stale_callback(event, data)
            logger.info("skip raced duplicate update_id=%s", update_id)
            return None

        return await handler(event, data)

    @staticmethod
    async def _ack_stale_callback(event: Update, data: dict[str, Any]) -> None:
        """Stop Telegram loading spinner on replayed callback_query."""
        cq = event.callback_query
        if cq is None:
            return
        bot: Bot | None = data.get("bot")
        if bot is None:
            return
        try:
            await bot.answer_callback_query(cq.id)
        except Exception:  # noqa: BLE001 — stale ack must not break polling
            logger.debug("stale callback ack failed update_id=%s", event.update_id, exc_info=True)
