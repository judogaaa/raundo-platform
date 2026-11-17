"""Startup PRO boosts: 24h cooldown, concurrency, eligibility, expiry cap."""

from __future__ import annotations

from datetime import timedelta

import pytest

from raundo.domain import REVIEW_APPROVED
from raundo.services.accounts import AccountService
from raundo.services.premium import PremiumService
from raundo.services.startup_boosts import BoostError, StartupBoostService
from raundo.services.startups import StartupService


async def _approved_startup_with_pro(session, telegram_id: int, name: str):
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="T",
        last_name=None,
        language_code="ru",
    )
    startup = await StartupService(session).create_draft(owner.id, name)
    premium = PremiumService(session)
    await premium.grant(
        plan_code="startup_pro",
        duration_code="30d",
        acting_user_id=owner.id,
        startup_id=startup.id,
        idempotency_key=f"b:{telegram_id}",
    )
    startup.review_state = REVIEW_APPROVED
    await session.flush()
    return owner, startup


@pytest.mark.asyncio
async def test_activate_then_24h_cooldown_blocks_second(session) -> None:
    owner, startup = await _approved_startup_with_pro(session, 201, "Cool")
    boosts = StartupBoostService(session)
    boost, created = await boosts.activate(
        startup_id=startup.id, acting_user_id=owner.id, idempotency_key="b1"
    )
    assert created
    ends = boost.ends_at - boost.started_at
    assert abs(ends.total_seconds() - 30 * 60) < 5  # 30-minute window
    with pytest.raises(BoostError) as exc:
        await boosts.activate(
            startup_id=startup.id, acting_user_id=owner.id, idempotency_key="b2"
        )
    assert exc.value.code in {"already_active", "cooldown"}
    # Close the active window → still blocked by the rolling 24h cooldown.
    boost_row = await boosts.last_boost(startup.id)
    assert boost_row is not None
    boost_row.status = "completed"
    boost_row.ends_at = boost_row.started_at
    await session.flush()
    with pytest.raises(BoostError) as exc2:
        await boosts.activate(
            startup_id=startup.id, acting_user_id=owner.id, idempotency_key="b2b"
        )
    assert exc2.value.code == "cooldown"
    # After 24h a new boost is allowed (previous window closed first).
    from datetime import timedelta as _td

    b1_row = await boosts.last_boost(startup.id)
    assert b1_row is not None
    b1_row.status = "completed"
    await session.flush()
    later = b1_row.started_at + _td(hours=24, minutes=1)
    boost2, created2 = await boosts.activate(
        startup_id=startup.id,
        acting_user_id=owner.id,
        idempotency_key="b3",
        now=later,
    )
    assert created2
    assert boost2.started_at >= later


@pytest.mark.asyncio
async def test_idempotent_retry_returns_same_boost(session) -> None:
    owner, startup = await _approved_startup_with_pro(session, 202, "Idem")
    boosts = StartupBoostService(session)
    b1, created1 = await boosts.activate(
        startup_id=startup.id, acting_user_id=owner.id, idempotency_key="same"
    )
    b2, created2 = await boosts.activate(
        startup_id=startup.id, acting_user_id=owner.id, idempotency_key="same"
    )
    assert created1 and not created2
    assert b1.id == b2.id


@pytest.mark.asyncio
async def test_foreign_user_cannot_boost(session) -> None:
    owner, startup = await _approved_startup_with_pro(session, 203, "Mine")
    stranger = await AccountService(session).bind_telegram_user(
        telegram_id=999,
        username="stranger",
        first_name="S",
        last_name=None,
        language_code="ru",
    )
    boosts = StartupBoostService(session)
    with pytest.raises(BoostError) as exc:
        await boosts.activate(
            startup_id=startup.id, acting_user_id=stranger.id, idempotency_key="x"
        )
    assert exc.value.code == "not_owner"


@pytest.mark.asyncio
async def test_unapproved_startup_cannot_boost(session) -> None:
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=204, username="u204", first_name="T", last_name=None, language_code="ru"
    )
    startup = await StartupService(session).create_draft(owner.id, "DraftOnly")
    premium = PremiumService(session)
    await premium.grant(
        plan_code="startup_pro",
        duration_code="30d",
        acting_user_id=owner.id,
        startup_id=startup.id,
        idempotency_key="b:204",
    )
    boosts = StartupBoostService(session)
    with pytest.raises(BoostError) as exc:
        await boosts.activate(
            startup_id=startup.id, acting_user_id=owner.id, idempotency_key="x"
        )
    assert exc.value.code == "not_approved"


@pytest.mark.asyncio
async def test_no_active_pro_no_boost(session) -> None:
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=205, username="u205", first_name="T", last_name=None, language_code="ru"
    )
    startup = await StartupService(session).create_draft(owner.id, "NoPro")
    startup.review_state = REVIEW_APPROVED
    await session.flush()
    boosts = StartupBoostService(session)
    with pytest.raises(BoostError) as exc:
        await boosts.activate(
            startup_id=startup.id, acting_user_id=owner.id, idempotency_key="x"
        )
    assert exc.value.code == "no_subscription"


@pytest.mark.asyncio
async def test_boost_never_outlives_subscription(session) -> None:
    owner, startup = await _approved_startup_with_pro(session, 206, "ShortSub")
    premium = PremiumService(session)
    sub = await premium.active_subscription("startup_pro", startup_id=startup.id)
    assert sub is not None
    # Subscription expires in 10 minutes → boost is clipped, not 30.
    from datetime import timedelta as _td

    sub.expires_at = utcnow_shift(10)
    await session.flush()
    boosts = StartupBoostService(session)
    boost, _ = await boosts.activate(
        startup_id=startup.id, acting_user_id=owner.id, idempotency_key="clip"
    )
    assert boost.ends_at <= sub.expires_at
    assert (boost.ends_at - boost.started_at) <= _td(minutes=10)


def utcnow_shift(minutes: int):
    from raundo.services.premium import utcnow

    return utcnow() + timedelta(minutes=minutes)


@pytest.mark.asyncio
async def test_complete_finished_sweep(session) -> None:
    owner, startup = await _approved_startup_with_pro(session, 207, "Sweep")
    boosts = StartupBoostService(session)
    boost, _ = await boosts.activate(
        startup_id=startup.id, acting_user_id=owner.id, idempotency_key="s1"
    )
    # Force the window into the past; sweep closes it and frees the cooldown path.
    boost.ends_at = boost.started_at
    await session.flush()
    closed = await boosts.complete_finished()
    await session.flush()
    assert closed == 1
    await session.refresh(boost)
    assert boost.status == "completed"
