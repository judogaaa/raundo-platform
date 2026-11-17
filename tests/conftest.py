"""Shared fixtures: in-memory SQLite for repository/service tests."""

from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.catalog_seed import ensure_catalog_seeded
from raundo.db.base import Base
from raundo.premium_catalog import PLAN_STARTUP_PRO
from raundo.services.premium import PremiumService


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db_session:
        await ensure_catalog_seeded(db_session)
        await db_session.commit()
        yield db_session
    await engine.dispose()


async def grant_startup_pro(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    startup_id: uuid.UUID,
    duration_code: str = "30d",
) -> object:
    """Test-fixture grant of Startup PRO bound to a specific startup."""
    sub, _created = await PremiumService(session).grant(
        plan_code=PLAN_STARTUP_PRO,
        duration_code=duration_code,
        acting_user_id=owner_user_id,
        startup_id=startup_id,
        idempotency_key=f"fixture-{uuid.uuid4()}",
        test_reason="test fixture",
    )
    return sub


@pytest_asyncio.fixture(autouse=True)
async def isolated_admin_connection_pool():
    """Never reuse asyncpg connections across pytest's per-test event loops."""
    from raundo.db import session as db_session_module

    yield
    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        db_session_module._cached_factory = None
