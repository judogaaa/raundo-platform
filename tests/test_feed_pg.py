"""PostgreSQL integration for the startup feed, reactions, favorites and 14-day top.

Runs only against the allowlisted suite DB (RAUNDO_POSTGRES_SUITE_TESTS=1).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.billing import UserCollection, UserCollectionItem
from raundo.models.feed import StartupReaction
from raundo.models.investor import InvestorProfile
from raundo.models.startup import Startup
from raundo.models.user import User
from raundo.services import feed as feed_service
from raundo.services.accounts import AccountService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 992_500


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    url = os.environ.get("DATABASE_URL", "")
    if "raundo_local_suite" not in url:
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean_suite(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old_users = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 100)
            )
        ).scalars().all()
        if old_users:
            old_startups = (
                await session.execute(
                    select(Startup.id).where(Startup.owner_user_id.in_(old_users))
                )
            ).scalars().all()
            if old_startups:
                await session.execute(
                    delete(StartupReaction).where(StartupReaction.startup_id.in_(old_startups))
                )
                await session.execute(delete(Startup).where(Startup.id.in_(old_startups)))
            await session.execute(
                delete(UserCollectionItem).where(
                    UserCollectionItem.collection_id.in_(
                        select(UserCollection.id).where(UserCollection.user_id.in_(old_users))
                    )
                )
            )
            await session.execute(delete(UserCollection).where(UserCollection.user_id.in_(old_users)))
            await session.execute(delete(InvestorProfile).where(InvestorProfile.user_id.in_(old_users)))
            await session.execute(delete(User).where(User.id.in_(old_users)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _user(factory, offset: int):  # noqa: ANN001, ANN202
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + offset,
            username=f"feed_user_{offset}",
            first_name="Feed",
            last_name=str(offset),
            language_code="ru",
        )
        await session.commit()
        return user.id


async def _startup(factory, owner_id: uuid.UUID, name: str, state: str = "approved") -> uuid.UUID:  # noqa: ANN001
    async with factory() as session:
        startup = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            slug=f"feed-{uuid.uuid4().hex[:10]}",
            name=name,
            review_state=state,
            current_revision=1,
            published_at=datetime.now(timezone.utc) if state == "approved" else None,
        )
        session.add(startup)
        await session.commit()
        return startup.id


def test_period_is_fixed_14_days_and_non_negative() -> None:
    period = feed_service.current_period()
    assert period.end - period.start == feed_service.timedelta(days=14)
    assert period.index >= 0
    early = feed_service.current_period(datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert early.index == 0


async def test_reaction_is_single_row_and_can_change(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 1)
    viewer = await _user(factory, 2)
    startup_id = await _startup(factory, owner, "Feed One")
    async with factory() as session:
        counts = await feed_service.react(
            session, user_id=viewer, startup_id=startup_id, kind="like"
        )
        await session.commit()
        assert counts == {"like": 1}
        counts = await feed_service.react(
            session, user_id=viewer, startup_id=startup_id, kind="dislike"
        )
        await session.commit()
        assert counts == {"dislike": 1}
    async with factory() as session:
        rows = (
            await session.execute(
                select(StartupReaction).where(StartupReaction.startup_id == startup_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].kind == "dislike"


async def test_self_vote_and_hidden_startups_are_rejected(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 3)
    startup_id = await _startup(factory, owner, "Feed Self")
    hidden_id = await _startup(factory, owner, "Feed Hidden", state="rejected")
    async with factory() as session:
        with pytest.raises(feed_service.FeedError):
            await feed_service.react(
                session, user_id=owner, startup_id=startup_id, kind="like"
            )
        with pytest.raises(feed_service.FeedError):
            await feed_service.react(
                session, user_id=await _user(factory, 4), startup_id=hidden_id, kind="like"
            )


async def test_top_counts_unique_likes_and_excludes_self(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 10)
    viewer = await _user(factory, 12)
    startup_id = await _startup(factory, owner, "Feed Top Winner")
    async with factory() as session:
        await feed_service.react(session, user_id=viewer, startup_id=startup_id, kind="like")
        # Owner's own like is refused by react(); emulate a legacy row to test exclusion.
        session.add(
            StartupReaction(
                id=uuid.uuid4(),
                startup_id=startup_id,
                user_id=owner,
                kind="like",
                period_index=feed_service.current_period().index,
            )
        )
        await session.commit()
        period, items = await feed_service.top_startups(session, cohort="community")
    assert period.index == feed_service.current_period().index
    assert items, "expected at least one ranked startup"
    assert str(items[0]["startup"].id) == str(startup_id)
    assert items[0]["likes"] == 1


async def test_verified_cohort_requires_approved_mandate(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 20)
    plain = await _user(factory, 21)
    verified = await _user(factory, 22)
    startup_id = await _startup(factory, owner, "Feed Verified")
    async with factory() as session:
        investor = InvestorProfile(
            id=uuid.uuid4(),
            user_id=verified,
            display_name="Verified",
            review_status="approved",
            claims_direct_investment=True,
        )
        session.add(investor)
        await session.commit()
        await feed_service.react(session, user_id=plain, startup_id=startup_id, kind="like")
        await feed_service.react(session, user_id=verified, startup_id=startup_id, kind="like")
        await session.commit()
        _, community = await feed_service.top_startups(session, cohort="community")
        _, verified_items = await feed_service.top_startups(session, cohort="verified_investors")
    assert community[0]["likes"] == 2
    assert len(verified_items) == 1
    assert verified_items[0]["likes"] == 1


async def test_favorites_toggle(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 30)
    viewer = await _user(factory, 31)
    startup_id = await _startup(factory, owner, "Feed Fav")
    async with factory() as session:
        added = await feed_service.toggle_favorite(
            session, user_id=viewer, startup_id=startup_id
        )
        await session.commit()
        assert added is True
        assert await feed_service.is_favorite(
            session, user_id=viewer, startup_id=startup_id
        )
        removed = await feed_service.toggle_favorite(
            session, user_id=viewer, startup_id=startup_id
        )
        await session.commit()
        assert removed is False
        assert not await feed_service.is_favorite(
            session, user_id=viewer, startup_id=startup_id
        )
