"""Premium subscriptions: grants, owner binding, expiry, extension, idempotency."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from raundo.models.billing import PremiumSubscription
from raundo.services.accounts import AccountService
from raundo.services.premium import (
    OWNER_ORGANIZATION,
    OWNER_STARTUP,
    OWNER_USER,
    PLAN_OWNER_TYPE,
    PremiumService,
    PremiumServiceError,
    utcnow,
)
from raundo.services.startups import StartupService

OWNER_BINDING = {
    "community_plus": OWNER_USER,
    "investor_pro": OWNER_USER,
    "startup_pro": OWNER_STARTUP,
    "organization_pro": OWNER_ORGANIZATION,
}


async def _user(session, telegram_id: int):
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="T",
        last_name=None,
        language_code="ru",
    )


async def _startup(session, owner):
    return await StartupService(session).create_draft(owner.id, "Boostable")


@pytest.mark.asyncio
async def test_owner_binding_per_plan(session) -> None:
    for plan, owner_type in OWNER_BINDING.items():
        assert PLAN_OWNER_TYPE[plan] == owner_type


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["community_plus", "investor_pro"])
async def test_grant_user_plan_with_exact_hours(session, plan: str) -> None:
    user = await _user(session, 101)
    premium = PremiumService(session)
    sub, created = await premium.grant(
        plan_code=plan,
        duration_code="3d",
        acting_user_id=user.id,
        idempotency_key=f"t:{plan}:1",
        test_reason="unit test",
    )
    await session.commit()
    assert created
    assert sub.owner_type == "user"
    assert sub.is_test
    expected = utcnow() + timedelta(hours=72)
    assert abs((sub.expires_at - expected).total_seconds()) < 5
    assert sub.starts_at.tzinfo is not None
    active = await premium.active_subscription(plan, user_id=user.id)
    assert active is not None and active.id == sub.id


@pytest.mark.asyncio
async def test_all_durations_compute_expected_expiry(session) -> None:
    user = await _user(session, 102)
    premium = PremiumService(session)
    for index, hours in enumerate((72, 168, 720)):
        sub, created = await premium.grant(
            plan_code="community_plus",
            duration_code=duration_code_for(hours),
            acting_user_id=user.id,
            idempotency_key=f"t:{hours}",
        )
        assert created
        expected = utcnow() + timedelta(hours=hours)
        assert abs((sub.expires_at - expected).total_seconds()) < 5
        await premium.cancel(sub)
    await session.flush()


def duration_code_for(hours: int) -> str:
    return {72: "3d", 168: "7d", 720: "30d"}[hours]


@pytest.mark.asyncio
async def test_double_active_same_plan_rejected_extending_allowed(session) -> None:
    user = await _user(session, 103)
    premium = PremiumService(session)
    sub, _ = await premium.grant(
        plan_code="investor_pro",
        duration_code="7d",
        acting_user_id=user.id,
        idempotency_key="t:1",
    )
    original_expiry = sub.expires_at
    with pytest.raises(PremiumServiceError) as exc:
        await premium.grant(
            plan_code="investor_pro",
            duration_code="3d",
            acting_user_id=user.id,
            idempotency_key="t:2",
        )
    assert exc.value.code == "already_active"
    await premium.extend(sub, "30d")
    assert sub.expires_at > original_expiry
    events = await premium.events_for(sub.id)
    assert [e.event_type for e in events] == ["granted", "extended"]


@pytest.mark.asyncio
async def test_regrant_after_expiry_and_sweep(session) -> None:
    user = await _user(session, 104)
    premium = PremiumService(session)
    sub, _ = await premium.grant(
        plan_code="community_plus",
        duration_code="3d",
        acting_user_id=user.id,
        idempotency_key="t:1",
    )
    # Simulate downtime: push expiry into the past, then sweep.
    sub.expires_at = utcnow() - timedelta(minutes=1)
    await session.flush()
    assert await premium.active_subscription("community_plus", user_id=user.id) is None
    expired_count = await premium.expire_overdue()
    await session.flush()
    assert expired_count == 1
    await session.refresh(sub)
    assert sub.status == "expired"
    events = await premium.events_for(sub.id)
    assert events[-1].event_type == "expired"
    # Re-grant after expiry creates a new window without touching history.
    sub2, created = await premium.grant(
        plan_code="community_plus",
        duration_code="7d",
        acting_user_id=user.id,
        idempotency_key="t:2",
    )
    assert created
    assert sub2.id != sub.id
    assert sub2.status == "active"


@pytest.mark.asyncio
async def test_idempotency_key_replay_and_conflict(session) -> None:
    user = await _user(session, 105)
    premium = PremiumService(session)
    sub1, created1 = await premium.grant(
        plan_code="community_plus",
        duration_code="3d",
        acting_user_id=user.id,
        idempotency_key="same-key",
    )
    sub2, created2 = await premium.grant(
        plan_code="community_plus",
        duration_code="3d",
        acting_user_id=user.id,
        idempotency_key="same-key",
    )
    assert created1 and not created2
    assert sub1.id == sub2.id
    with pytest.raises(PremiumServiceError) as exc:
        await premium.cancel(sub1)
        await premium.grant(
            plan_code="investor_pro",  # same key, different params
            duration_code="3d",
            acting_user_id=user.id,
            idempotency_key="same-key",
        )
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.asyncio
async def test_startup_pro_bound_to_foreign_startup_denied(session) -> None:
    import random as _random

    owner = await _user(session, _random.randint(900_100_000, 900_199_999))
    foreign = await _user(session, _random.randint(900_200_000, 900_299_999))
    startup = await _startup(session, owner)
    premium = PremiumService(session)
    with pytest.raises(PremiumServiceError) as exc:
        await premium.grant(
            plan_code="startup_pro",
            duration_code="30d",
            acting_user_id=foreign.id,
            startup_id=startup.id,
            idempotency_key="t:foreign",
        )
    assert exc.value.code == "owner_mismatch"
    # Correct owner succeeds; entitlement is scoped to that exact startup.
    sub, created = await premium.grant(
        plan_code="startup_pro",
        duration_code="30d",
        acting_user_id=owner.id,
        startup_id=startup.id,
        idempotency_key="t:ok",
    )
    assert created
    assert await premium.has_active_startup_pro(startup.id)
    other = await _startup(session, foreign)
    assert not await premium.has_active_startup_pro(other.id)


@pytest.mark.asyncio
async def test_multi_role_user_holds_independent_plans(session) -> None:
    user = await _user(session, 108)
    premium = PremiumService(session)
    for plan in ("community_plus", "investor_pro"):
        await premium.grant(
            plan_code=plan,
            duration_code="3d",
            acting_user_id=user.id,
            idempotency_key=f"t:{plan}",
        )
    ent = await premium.user_entitlements(user.id)
    assert set(ent) == {"community_plus", "investor_pro"}


@pytest.mark.asyncio
async def test_no_payment_means_no_subscription(session) -> None:
    """Without a grant or a future payment layer, nobody owns Premium."""
    user = await _user(session, 109)
    premium = PremiumService(session)
    rows = (
        await session.execute(select(PremiumSubscription))
    ).scalars().all()
    assert rows == []
    assert await premium.has_active_user_plan(user.id, "community_plus") is False
    assert await premium.my_subscriptions(user.id) == []


@pytest.mark.asyncio
async def test_unknown_plan_or_duration_rejected(session) -> None:
    user = await _user(session, 110)
    premium = PremiumService(session)
    for plan, duration in (("ultra", "3d"), ("community_plus", "365d")):
        with pytest.raises(PremiumServiceError):
            await premium.grant(
                plan_code=plan,
                duration_code=duration,
                acting_user_id=user.id,
                idempotency_key=f"t:{plan}:{duration}",
            )


@pytest.mark.asyncio
async def test_grant_events_are_audited(session) -> None:
    user = await _user(session, 111)
    premium = PremiumService(session)
    sub, _ = await premium.grant(
        plan_code="community_plus",
        duration_code="3d",
        acting_user_id=user.id,
        idempotency_key="t:audit",
        test_reason="audit check",
    )
    events = await premium.events_for(sub.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "granted"
    assert event.safe_payload["plan"] == "community_plus"
    assert event.safe_payload["is_test"] is True
    assert isinstance(event.id, uuid.UUID)
    assert event.actor_user_id == user.id
