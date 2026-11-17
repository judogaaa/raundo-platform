"""Dispatcher wiring shared by runtime and integration tests."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from raundo.middlewares.auth import AuthMiddleware
from raundo.middlewares.db import DbSessionMiddleware
from raundo.middlewares.idempotency import IdempotencyMiddleware
from raundo.middlewares.rate_limit import RateLimitMiddleware
from raundo.middlewares.restriction import RestrictionMiddleware
from raundo.routers import build_root_router


def setup_dispatcher(
    *,
    storage: BaseStorage,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    rate_limit: int = 30,
    rate_limit_window_seconds: int = 10,
) -> Dispatcher:
    """
    Register middleware on Update so feed_update/real Telegram path injects db_user.

    Order (outer → inner): DB session → idempotency → Auth (db_user) → restriction
    → rate limit → handlers.
    """
    dispatcher = Dispatcher(storage=storage)
    dispatcher.update.middleware(DbSessionMiddleware(session_factory))
    dispatcher.update.middleware(IdempotencyMiddleware())
    dispatcher.update.middleware(AuthMiddleware())
    dispatcher.update.middleware(RestrictionMiddleware())
    dispatcher.update.middleware(
        RateLimitMiddleware(
            redis,
            limit=rate_limit,
            window_seconds=rate_limit_window_seconds,
        )
    )
    dispatcher.include_router(build_root_router())
    return dispatcher
