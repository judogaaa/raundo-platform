"""Outbox delivery worker (no Telegram polling, only Bot API sendMessage).

Reads configuration from the process environment (see scripts/start-worker.ps1).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from raundo.config import get_settings
from raundo.db.session import get_session_factory
from raundo.runtime_info import resolve_git_sha
from raundo.services.outbox_worker import (
    OPTIONAL_NOTIFICATION_KINDS,
    TelegramBlockedError,
    TelegramRateLimitError,
    deliver_pending,
)
from raundo.telegram_transport import build_bot as build_telegram_bot
from raundo.telegram_transport import ensure_proxy_policy

INTERVAL_SECONDS = int(os.environ.get("RAUNDO_OUTBOX_INTERVAL", "5"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outbox-worker")

_stop = asyncio.Event()


class AiogramSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def _send(self, telegram_id: int, text: str, markup=None) -> None:  # noqa: ANN001
        try:
            # Notifications are composed as plain text: pass parse_mode explicitly
            # so a future global HTML default cannot interpret '<', '>' or '&'
            # from user comments as Telegram entities.
            await self._bot.send_message(
                telegram_id, text, parse_mode=None, reply_markup=markup
            )
        except TelegramRetryAfter as exc:
            raise TelegramRateLimitError(retry_after=int(exc.retry_after)) from exc
        except TelegramForbiddenError as exc:
            raise TelegramBlockedError(str(exc)) from exc

    async def send_text(self, telegram_id: int, text: str) -> None:
        await self._send(telegram_id, text)

    async def send_with_buttons(
        self, telegram_id: int, text: str, buttons: tuple[tuple[str, str], ...]
    ) -> None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=data)]
                for label, data in buttons
            ]
        )
        await self._send(telegram_id, text, markup)


async def main() -> None:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is required for outbox delivery")
    settings = get_settings()
    proxy = ensure_proxy_policy(settings)
    database_url = os.environ.get("DATABASE_URL", "")
    log.info(
        "worker_start db=%s interval=%ss sha=%s egress=%s api=%s optional_kinds=%s",
        database_url.rsplit("/", 1)[-1],
        INTERVAL_SECONDS,
        resolve_git_sha() or "unknown",
        "proxy" if proxy else "direct",
        "test" if settings.telegram_test_mode else "production",
        ",".join(sorted(OPTIONAL_NOTIFICATION_KINDS)),
    )

    bot = build_telegram_bot(
        token,
        parse_mode=None,
        proxy=proxy,
        test_mode=settings.telegram_test_mode,
    )
    sender = AiogramSender(bot)
    factory = get_session_factory()
    try:
        while not _stop.is_set():
            try:
                async with factory() as session:
                    sent = await deliver_pending(session, sender)
                    await session.commit()
                if sent:
                    log.info("delivered=%s", sent)
            except Exception:  # noqa: BLE001
                log.exception("outbox_cycle_failed")
            try:
                await asyncio.wait_for(_stop.wait(), timeout=INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        await bot.session.close()
        log.info("worker_stopped")


def _handle_signal(*_: object) -> None:
    _stop.set()


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):
        pass
    asyncio.run(main())
