"""Versioned premium catalog sync: price changes never rewrite history.

Configuration-driven price updates create a NEW active offer version and
deactivate the previous one. Existing subscriptions keep their
price_minor_at_purchase snapshot — no retroactive rewriting.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import PremiumPlanOffer
from raundo.premium_catalog import PRICE_CURRENCY, is_known_duration, is_known_plan


class PremiumCatalogError(Exception):
    pass


class PremiumCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[PremiumPlanOffer]:
        result = await self._session.scalars(
            select(PremiumPlanOffer)
            .where(PremiumPlanOffer.is_active.is_(True))
            .order_by(PremiumPlanOffer.plan_code, PremiumPlanOffer.duration_code)
        )
        return list(result.all())

    async def get_active(self, plan_code: str, duration_code: str) -> PremiumPlanOffer | None:
        return (
            await self._session.scalars(
                select(PremiumPlanOffer).where(
                    PremiumPlanOffer.plan_code == plan_code,
                    PremiumPlanOffer.duration_code == duration_code,
                    PremiumPlanOffer.is_active.is_(True),
                )
            )
        ).first()

    async def set_price(
        self, plan_code: str, duration_code: str, price_minor: int, *, currency: str = PRICE_CURRENCY
    ) -> PremiumPlanOffer:
        """Deactivate current offer, add version+1 with the new price."""
        if not is_known_plan(plan_code) or not is_known_duration(duration_code):
            raise PremiumCatalogError("Неизвестный тариф или срок.")
        if price_minor <= 0:
            raise PremiumCatalogError("Цена должна быть положительной.")
        current = await self.get_active(plan_code, duration_code)
        if current is not None:
            if current.price_minor == price_minor and current.currency == currency:
                return current
            current.is_active = False
            await self._session.flush()
        next_version = (current.version + 1) if current is not None else 1
        offer = PremiumPlanOffer(
            plan_code=plan_code,
            duration_code=duration_code,
            duration_hours=current.duration_hours if current else 0,
            price_minor=price_minor,
            currency=currency,
            version=next_version,
            is_active=True,
        )
        self._session.add(offer)
        await self._session.flush()
        return offer
