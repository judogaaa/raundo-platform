"""Startup PRO boosts: 30-minute promotion window, once per 24 h.

Storage, eligibility rules and concurrency protection are complete; the real
extra-impression mechanism arrives with the discovery feed (Issue #3), so no
Telegram activation UI is wired yet — activating a boost without real extra
impressions would be an imitation of a paid effect.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import (
    REVIEW_APPROVED,
    boost_cooldown_over,
    boost_end_time,
)
from raundo.models.billing import PremiumSubscription, StartupBoost
from raundo.models.startup import Startup
from raundo.premium_catalog import PLAN_STARTUP_PRO
from raundo.services.premium import SUB_STATUS_ACTIVE, ensure_utc, utcnow


class BoostError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class StartupBoostService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_boost(self, startup_id: uuid.UUID) -> Optional[StartupBoost]:
        result = await self._session.scalars(
            select(StartupBoost)
            .where(
                StartupBoost.startup_id == startup_id,
                StartupBoost.status == "active",
                StartupBoost.ends_at > utcnow(),
            )
            .limit(1)
        )
        return result.first()

    async def last_boost(self, startup_id: uuid.UUID) -> Optional[StartupBoost]:
        return (
            await self._session.scalars(
                select(StartupBoost)
                .where(StartupBoost.startup_id == startup_id)
                .order_by(StartupBoost.started_at.desc())
                .limit(1)
            )
        ).first()

    async def activate(
        self,
        *,
        startup_id: uuid.UUID,
        acting_user_id: uuid.UUID,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> tuple[StartupBoost, bool]:
        """
        Activate one promotion window.

        Rules enforced server-side under a row lock on the startup:
        - only the owner's own startup;
        - only an admin-approved startup (review_state=approved);
        - an active Startup PRO subscription bound to THIS startup;
        - one boost per rolling 24 h (no stacking);
        - the window never outlives the subscription (min(+30min, expires_at));
        - idempotent retries return the existing boost.
        """
        current = now or utcnow()
        # Serialize concurrent activations for the same startup.
        await self._session.execute(
            select(Startup.id).where(Startup.id == startup_id).with_for_update()
        )
        startup = await self._session.get(Startup, startup_id)
        if startup is None:
            raise BoostError("Стартап не найден.", code="not_found")
        if startup.owner_user_id != acting_user_id:
            raise BoostError("Продвигать можно только свой стартап.", code="not_owner")
        if startup.review_state != REVIEW_APPROVED:
            raise BoostError(
                "Продвижение доступно только одобренному и опубликованному стартапу.",
                code="not_approved",
            )

        if idempotency_key:
            existing = (
                await self._session.scalars(
                    select(StartupBoost).where(
                        StartupBoost.idempotency_key == idempotency_key
                    )
                )
            ).first()
            if existing is not None:
                if existing.user_id != acting_user_id or existing.startup_id != startup_id:
                    raise BoostError(
                        "Idempotency-ключ уже использован с другими параметрами.",
                        code="idempotency_conflict",
                    )
                return existing, False

        subscription = (
            await self._session.scalars(
                select(PremiumSubscription)
                .where(
                    PremiumSubscription.plan_code == PLAN_STARTUP_PRO,
                    PremiumSubscription.owner_type == "startup",
                    PremiumSubscription.startup_id == startup_id,
                    PremiumSubscription.status == SUB_STATUS_ACTIVE,
                    PremiumSubscription.expires_at > current,
                )
                .order_by(PremiumSubscription.expires_at.desc())
                .limit(1)
            )
        ).first()
        if subscription is None:
            raise BoostError("Требуется активный Startup PRO.", code="no_subscription")

        active = await self.active_boost(startup_id)
        if active is not None:
            raise BoostError(
                "Продвижение уже идёт. Бусты не накапливаются.", code="already_active"
            )

        last = await self.last_boost(startup_id)
        last_started = ensure_utc(last.started_at) if last else None
        if not boost_cooldown_over(current, last_started):
            raise BoostError(
                "Следующее продвижение доступно через 24 часа после предыдущего.",
                code="cooldown",
            )

        boost = StartupBoost(
            id=uuid.uuid4(),
            startup_id=startup_id,
            subscription_id=subscription.id,
            user_id=acting_user_id,
            status="active",
            started_at=current,
            ends_at=boost_end_time(
                started_at=current,
                subscription_expires_at=ensure_utc(subscription.expires_at),
            ),
            idempotency_key=idempotency_key,
            created_at=current,
        )
        self._session.add(boost)
        await self._session.flush()
        return boost, True

    async def complete_finished(self, now: datetime | None = None) -> int:
        """Sweep: close active windows whose time is over (feed/job will call)."""
        current = now or datetime.now(timezone.utc)
        result = await self._session.execute(
            select(StartupBoost).where(
                StartupBoost.status == "active",
                StartupBoost.ends_at <= current,
            )
        )
        rows = list(result.scalars().all())
        for boost in rows:
            boost.status = "completed"
        if rows:
            await self._session.flush()
        return len(rows)
