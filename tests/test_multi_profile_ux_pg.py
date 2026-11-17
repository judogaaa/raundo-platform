"""Telegram handler UX for multiple investor profiles (2nd/3rd via menu)."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.billing import PremiumSubscription
from raundo.models.investor import InvestorProfile
from raundo.models.user import User
from raundo.routers import investor as investor_router
from raundo.services.accounts import AccountService
from raundo.services.admin_directory import user_detail
from raundo.services.profile_limits import limits_for_user
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_200


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    if "raundo_local_suite" not in os.environ.get("DATABASE_URL", ""):
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs) -> None:  # noqa: ANN003
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 50)
            )
        ).scalars().all()
        if old:
            await session.execute(delete(PremiumSubscription).where(PremiumSubscription.user_id.in_(old)))
            await session.execute(delete(InvestorProfile).where(InvestorProfile.user_id.in_(old)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _user_with_pro(factory):  # noqa: ANN001, ANN202
    from datetime import datetime, timedelta, timezone

    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="multi_investor",
            first_name="Multi",
            last_name="Investor",
            language_code="ru",
        )
        now = datetime.now(timezone.utc)
        session.add(
            PremiumSubscription(
                id=uuid.uuid4(),
                owner_type="user",
                user_id=user.id,
                plan_code="investor_pro",
                duration_code="30d",
                price_minor_at_purchase=0,
                currency_at_purchase="RUB",
                status="active",
                source="test_grant",
                is_test=True,
                test_reason="multi profile UX",
                idempotency_key=f"multi:{user.id}",
                starts_at=now,
                expires_at=now + timedelta(days=30),
            )
        )
        await session.commit()
        return user.id


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


async def test_three_investor_profiles_via_handlers_and_fourth_blocked(factory) -> None:  # noqa: ANN001
    user_id = await _user_with_pro(factory)
    state = FakeState()
    created: list[uuid.UUID] = []
    async with factory() as session:
        user = await session.get(User, user_id)
        for _ in range(3):
            callback = _callback("inv:new")
            await investor_router.on_investor_new(callback, session, user, state)
            created.append(uuid.UUID(state.data["investor_id"]))
        assert len(set(created)) == 3
        callback = _callback("inv:new")
        await investor_router.on_investor_new(callback, session, user, state)
        callback.answer.assert_awaited()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        limits = await limits_for_user(session, user_id)
        assert (limits.investor.used, limits.investor.limit) == (3, 3)

    # Editing the second profile must not touch the first or third.
    async with factory() as session:
        first = await session.get(InvestorProfile, created[0])
        second = await session.get(InvestorProfile, created[1])
        second.display_name = "Second Profile"
        await session.commit()
        first_name = first.display_name
        assert first_name != "Second Profile"

    # Switching via inline callbacks stores the selected profile id.
    third = created[2]
    async with factory() as session:
        user = await session.get(User, user_id)
        callback = _callback(f"inv:sel:{third}")
        await investor_router.on_investor_select(callback, session, user, state)
        assert state.data["investor_id"] == str(third)

    # Archive + restore keeps content and respects the quota.
    async with factory() as session:
        user = await session.get(User, user_id)
        second = await session.get(InvestorProfile, created[1])
        await investor_router.InvestorService(session).delete_owned(second, user.id)
        await session.commit()
        limits = await limits_for_user(session, user_id)
        assert limits.investor.used == 2
        callback = _callback(f"inv:res:{created[1]}")
        await investor_router.on_investor_restore(callback, session, user, state)
        restored = await session.get(InvestorProfile, created[1])
        assert restored.deleted_at is None
        assert restored.display_name == "Second Profile"

    # Admin sees all investor profiles.
    async with factory() as session:
        detail = await user_detail(session, user_id)
    assert len(detail["investors"]) == 3
    assert {p["display_name"] for p in detail["investors"]} >= {"Second Profile"}
