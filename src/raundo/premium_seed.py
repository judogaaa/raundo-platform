"""Premium offer seed shared by migration 20261026_0005 and tests.

Create-only: existing active offers are never rewritten here. Price changes
must go through PremiumCatalogService versioning (history preserved).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import PremiumPlanOffer
from raundo.premium_catalog import PRICE_CURRENCY, all_offers


async def ensure_premium_offers_seeded(session: AsyncSession) -> int:
    """Insert missing active offers; returns number of rows added."""
    result = await session.execute(
        select(PremiumPlanOffer.plan_code, PremiumPlanOffer.duration_code).where(
            PremiumPlanOffer.is_active.is_(True)
        )
    )
    existing = {(row.plan_code, row.duration_code) for row in result.all()}
    added = 0
    for plan_code, duration_code, duration_hours, price_minor in all_offers():
        if (plan_code, duration_code) in existing:
            continue
        session.add(
            PremiumPlanOffer(
                plan_code=plan_code,
                duration_code=duration_code,
                duration_hours=duration_hours,
                price_minor=price_minor,
                currency=PRICE_CURRENCY,
                version=1,
                is_active=True,
            )
        )
        added += 1
    if added:
        await session.flush()
    return added
