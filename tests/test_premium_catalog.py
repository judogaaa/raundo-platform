"""Premium catalog: 4 plans × 3 durations = 12 offers (D014)."""

from __future__ import annotations

import pytest

from raundo.premium_catalog import (
    DURATION_CODES,
    DURATION_HOURS,
    PLAN_CODES,
    PLAN_COMMUNITY_PLUS,
    PLAN_INVESTOR_PRO,
    PLAN_ORGANIZATION_PRO,
    PLAN_STARTUP_PRO,
    format_price,
    is_known_duration,
    is_known_plan,
    price_minor,
)
from raundo.premium_seed import ensure_premium_offers_seeded
from raundo.services.premium_catalog import PremiumCatalogError, PremiumCatalogService

EXPECTED_PRICES_MINOR = {
    (PLAN_COMMUNITY_PLUS, "3d"): 4_900,
    (PLAN_COMMUNITY_PLUS, "7d"): 9_900,
    (PLAN_COMMUNITY_PLUS, "30d"): 29_900,
    (PLAN_STARTUP_PRO, "3d"): 14_900,
    (PLAN_STARTUP_PRO, "7d"): 29_900,
    (PLAN_STARTUP_PRO, "30d"): 79_900,
    (PLAN_INVESTOR_PRO, "3d"): 69_900,
    (PLAN_INVESTOR_PRO, "7d"): 149_000,
    (PLAN_INVESTOR_PRO, "30d"): 499_000,
    (PLAN_ORGANIZATION_PRO, "3d"): 149_000,
    (PLAN_ORGANIZATION_PRO, "7d"): 299_000,
    (PLAN_ORGANIZATION_PRO, "30d"): 999_000,
}


def test_exactly_twelve_offers_with_canonical_prices() -> None:
    offers = {(plan, dur): price_minor(plan, dur) for plan in PLAN_CODES for dur in DURATION_CODES}
    assert len(PLAN_CODES) == 4
    assert len(DURATION_CODES) == 3
    assert set(offers) == set(EXPECTED_PRICES_MINOR)
    assert offers == EXPECTED_PRICES_MINOR


def test_durations_are_exact_hours() -> None:
    assert DURATION_HOURS == {"3d": 72, "7d": 168, "30d": 720}


def test_unknown_plan_and_duration_rejected() -> None:
    assert not is_known_plan("ultra_pro")
    assert not is_known_duration("365d")
    assert price_minor("ultra_pro", "3d") is None
    assert price_minor(PLAN_STARTUP_PRO, "365d") is None
    assert price_minor(PLAN_STARTUP_PRO, "3d") == 14_900


def test_format_price_is_human_rub() -> None:
    assert format_price(4_900) == "49 ₽"
    assert format_price(149_000) == "1 490 ₽"
    assert format_price(999_000) == "9 990 ₽"


async def test_seed_creates_all_twelve_once(session) -> None:
    added_first = await ensure_premium_offers_seeded(session)
    await session.commit()
    added_second = await ensure_premium_offers_seeded(session)
    await session.commit()
    assert added_first == 12
    assert added_second == 0


async def test_price_change_creates_new_version_keeps_history(session) -> None:
    """Catalog versioning: subscriptions keep their purchase-time snapshot."""
    await ensure_premium_offers_seeded(session)
    catalog = PremiumCatalogService(session)
    current = await catalog.get_active(PLAN_STARTUP_PRO, "30d")
    assert current is not None and current.version == 1
    updated = await catalog.set_price(PLAN_STARTUP_PRO, "30d", 89_900)
    assert updated.version == 2
    assert updated.is_active
    old = await session.get(type(current), current.id)
    assert old is not None and not old.is_active
    # Unknown combinations stay impossible.
    with pytest.raises(PremiumCatalogError):
        await catalog.set_price("nope", "3d", 100)
    with pytest.raises(PremiumCatalogError):
        await catalog.set_price(PLAN_STARTUP_PRO, "3d", 0)
