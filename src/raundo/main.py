"""Start local Telegram polling bot. No live operation without BotFather credentials."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from raundo.app import setup_dispatcher
from raundo.bot_commands import PUBLIC_BOT_COMMANDS
from raundo.config import get_settings
from raundo.db.session import create_engine, create_session_factory, dispose_engine
from raundo.logging_setup import configure_logging
from raundo.runtime_info import APP_NAME, PROCESS_STARTED_AT, resolve_git_sha
from raundo.telegram_transport import api_environment, ensure_proxy_policy
from raundo.telegram_transport import build_bot as build_telegram_bot

logger = logging.getLogger(__name__)


def build_bot(token: str, proxy: str | None = None, *, test_mode: bool = False) -> Bot:
    """Bot factory with the product-wide HTML parse mode."""
    return build_telegram_bot(
        token,
        parse_mode=ParseMode.HTML,
        proxy=proxy,
        test_mode=test_mode,
    )


async def main() -> None:
    settings = get_settings()
    settings.require_valid_runtime()
    configure_logging(settings.log_level)
    sha = resolve_git_sha() or "unknown"
    logger.info(
        "%s runtime pid=%s started=%s sha=%s",
        APP_NAME,
        os.getpid(),
        PROCESS_STARTED_AT.isoformat(),
        sha,
    )

    # SQL echo is opt-in only: default off so user field values are not dumped to logs.
    engine = create_engine(settings.database_url, echo=settings.sql_echo)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis)

    # HTML is the product-wide default: every dynamic value must be escaped with
    # raundo.ui.esc before it is interpolated into a message.
    proxy = ensure_proxy_policy(settings)
    bot = build_bot(
        settings.bot_token,
        proxy=proxy,
        test_mode=settings.telegram_test_mode,
    )
    logger.info(
        "telegram parse_mode=%s egress=%s api=%s",
        bot.default.parse_mode,
        "proxy" if proxy else "direct",
        api_environment(bot),
    )
    if settings.telegram_test_mode:
        logger.warning(
            "TELEGRAM TEST ENVIRONMENT: test users and test Stars only; "
            "no production chat is reachable"
        )
    dispatcher = setup_dispatcher(
        storage=storage,
        session_factory=session_factory,
        redis=redis,
        rate_limit=settings.rate_limit_per_window,
        rate_limit_window_seconds=settings.rate_limit_window_seconds,
    )

    await bot.set_my_commands(PUBLIC_BOT_COMMANDS)
    logger.info(
        "RAUNDO bot starting in %s mode (env=%s, policy=%s)",
        settings.bot_mode,
        settings.environment,
        settings.policy_version,
    )
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await dispatcher.storage.close()
        await redis.aclose()
        await bot.session.close()
        await dispose_engine(engine)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
