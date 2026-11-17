"""Async engine and session factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


_cached_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Process-wide factory for the admin API. Uses DATABASE_URL from settings."""
    global _cached_factory
    if _cached_factory is None:
        from raundo.config import get_settings

        settings = get_settings()
        url = settings.database_url.rstrip("/")
        db_name = url.rsplit("/", 1)[-1]
        if db_name == "raundo":
            live_ok = (
                settings.raundo_env == "local-live"
                and settings.allow_live_db is True
            )
            if not live_ok:
                raise RuntimeError(
                    "Admin API refuses live database 'raundo'. "
                    "Use raundo_admin_dev, or explicitly enable local-live mode "
                    "(RAUNDO_ENV=local-live and RAUNDO_ALLOW_LIVE_DB=1)."
                )
        engine = create_engine(settings.database_url, echo=settings.sql_echo)
        _cached_factory = create_session_factory(engine)
    return _cached_factory


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
