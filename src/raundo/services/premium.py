"""Premium subscriptions: grants, entitlements, expiry, history (Issue #5).

No payments are connected. Subscriptions appear only from audited test grants
(dual-gated: config + admin allowlist, done at the router layer) or, later,
from a verified payment layer that supplies an idempotency key. Entitlements
are always computed from status + expires_at on the server — never from
cached booleans or Telegram UI.

Owner binding is explicit per plan:
- community_plus / investor_pro → owner_type='user' (acting human)
- startup_pro → owner_type='startup' (that founder's specific startup)
- organization_pro → owner_type='organization' (verified org, seats gated)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import PremiumEvent, PremiumPlanOffer, PremiumSubscription
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup
from raundo.premium_catalog import (
    DURATION_HOURS,
    PLAN_COMMUNITY_PLUS,
    PLAN_INVESTOR_PRO,
    PLAN_ORGANIZATION_PRO,
    PLAN_STARTUP_PRO,
    is_known_duration,
    is_known_plan,
)
from raundo.premium_seed import ensure_premium_offers_seeded

SUB_STATUS_ACTIVE = "active"
SUB_STATUS_EXPIRED = "expired"
SUB_STATUS_CANCELLED = "cancelled"

OWNER_USER = "user"
OWNER_STARTUP = "startup"
OWNER_ORGANIZATION = "organization"

SOURCE_TEST_GRANT = "test_grant"

MEMBERSHIP_APPROVED = "approved"
MEMBERSHIP_REVOKED = "revoked"

PLAN_OWNER_TYPE: dict[str, str] = {
    PLAN_COMMUNITY_PLUS: OWNER_USER,
    PLAN_INVESTOR_PRO: OWNER_USER,
    PLAN_STARTUP_PRO: OWNER_STARTUP,
    PLAN_ORGANIZATION_PRO: OWNER_ORGANIZATION,
}


class PremiumServiceError(Exception):
    def __init__(self, message: str, *, code: str = "premium_error") -> None:
        self.code = code
        super().__init__(message)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite stores naive UTC; PostgreSQL returns aware — normalize for Python compares."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _active_filter():
    return (PremiumSubscription.status == SUB_STATUS_ACTIVE) & (
        PremiumSubscription.expires_at > utcnow()
    )


class PremiumService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ reads

    async def active_subscription(
        self,
        plan_code: str,
        *,
        user_id: uuid.UUID | None = None,
        startup_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
    ) -> Optional[PremiumSubscription]:
        """Server-side entitlement check: active status AND expires_at in future."""
        stmt = (
            select(PremiumSubscription)
            .where(
                PremiumSubscription.plan_code == plan_code,
                PremiumSubscription.status == SUB_STATUS_ACTIVE,
            )
            .order_by(PremiumSubscription.expires_at.desc())
        )
        if user_id is not None:
            stmt = stmt.where(PremiumSubscription.user_id == user_id)
        if startup_id is not None:
            stmt = stmt.where(PremiumSubscription.startup_id == startup_id)
        if organization_id is not None:
            stmt = stmt.where(PremiumSubscription.organization_id == organization_id)
        now = utcnow()
        for row in (await self._session.scalars(stmt)).all():
            expires = ensure_utc(row.expires_at)
            if expires is not None and expires > now:
                return row
        return None

    async def user_entitlements(self, user_id: uuid.UUID) -> dict[str, PremiumSubscription]:
        """All currently active user-owned plans for one human (multi-role safe)."""
        result = await self._session.scalars(
            select(PremiumSubscription)
            .where(
                PremiumSubscription.owner_type == OWNER_USER,
                PremiumSubscription.user_id == user_id,
                _active_filter(),
            )
        )
        return {sub.plan_code: sub for sub in result.all()}

    async def has_active_user_plan(
        self, user_id: uuid.UUID, plan_code: str
    ) -> bool:
        return await self.active_subscription(plan_code, user_id=user_id) is not None

    async def has_active_startup_pro(self, startup_id: uuid.UUID) -> bool:
        if (
            await self.active_subscription(PLAN_STARTUP_PRO, startup_id=startup_id)
            is not None
        ):
            return True
        # Owner-granted account-wide plan (owner_type='user') covers every
        # startup of that account, including ones created later.
        from raundo.models.startup import Startup

        owner_id = await self._session.scalar(
            select(Startup.owner_user_id).where(Startup.id == startup_id)
        )
        if owner_id is None:
            return False
        return PLAN_STARTUP_PRO in await self.user_entitlements(owner_id)

    async def has_active_org_pro(self, organization_id: uuid.UUID) -> bool:
        return (
            await self.active_subscription(
                PLAN_ORGANIZATION_PRO, organization_id=organization_id
            )
            is not None
        )

    async def my_subscriptions(self, user_id: uuid.UUID) -> list[PremiumSubscription]:
        """Everything this human owns or acts on, newest first, for /premium UI."""
        result = await self._session.scalars(
            select(PremiumSubscription)
            .where(PremiumSubscription.user_id == user_id)
            .order_by(PremiumSubscription.created_at.desc())
            .limit(50)
        )
        subs = list(result.all())
        for sub in subs:
            expires = ensure_utc(sub.expires_at)
            if sub.status == SUB_STATUS_ACTIVE and expires is not None and expires <= utcnow():
                sub.status = SUB_STATUS_EXPIRED
        return subs

    # ----------------------------------------------------------------- writes

    async def grant(
        self,
        *,
        plan_code: str,
        duration_code: str,
        acting_user_id: uuid.UUID,
        startup_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        is_test: bool = True,
        source: str = SOURCE_TEST_GRANT,
        test_reason: str | None = None,
        granted_by_telegram_id: int | None = None,
        price_minor: int | None = None,
        starts_at: datetime | None = None,
    ) -> tuple[PremiumSubscription, bool]:
        """
        Create one subscription window. Returns (subscription, created).

        - Owner binding validated server-side per plan.
        - Idempotent on idempotency_key (unique in DB); conflicting reuse of a
          key with different parameters is rejected, not silently accepted.
        - A second active subscription of the same plan for the same owner is
          rejected: renew via `extend`, re-grant after expiry.
        """
        if not is_known_plan(plan_code):
            raise PremiumServiceError("Неизвестный тариф.", code="unknown_plan")
        if not is_known_duration(duration_code):
            raise PremiumServiceError("Неизвестный срок подписки.", code="unknown_duration")

        owner_type = PLAN_OWNER_TYPE[plan_code]
        if owner_type == OWNER_STARTUP:
            if startup_id is None:
                raise PremiumServiceError(
                    "Startup PRO должен быть привязан к конкретному стартапу.",
                    code="missing_owner",
                )
            startup = await self._session.get(Startup, startup_id)
            if startup is None or startup.owner_user_id != acting_user_id:
                raise PremiumServiceError(
                    "Startup PRO можно оформить только на свой стартап.",
                    code="owner_mismatch",
                )
        elif owner_type == OWNER_ORGANIZATION:
            if organization_id is None:
                raise PremiumServiceError(
                    "Organization PRO должен быть привязан к организации.",
                    code="missing_owner",
                )
            org = await self._session.get(FundingOrganization, organization_id)
            if org is None:
                raise PremiumServiceError("Организация не найдена.", code="missing_owner")
        else:  # OWNER_USER
            if startup_id is not None or organization_id is not None:
                raise PremiumServiceError(
                    "Этот тариф оформляется на пользователя.", code="owner_mismatch"
                )

        if idempotency_key:
            existing = (
                await self._session.scalars(
                    select(PremiumSubscription).where(
                        PremiumSubscription.idempotency_key == idempotency_key
                    )
                )
            ).first()
            if existing is not None:
                _ensure_same_request(
                    existing,
                    plan_code=plan_code,
                    duration_code=duration_code,
                    acting_user_id=acting_user_id,
                    startup_id=startup_id,
                    organization_id=organization_id,
                )
                return existing, False

        current = await self.active_subscription(
            plan_code,
            user_id=acting_user_id if owner_type == OWNER_USER else None,
            startup_id=startup_id,
            organization_id=organization_id,
        )
        if current is not None:
            raise PremiumServiceError(
                "Активная подписка этого тарифа уже существует. Доступно продление.",
                code="already_active",
            )

        if price_minor is None:
            offer = await self._resolve_offer(plan_code, duration_code)
            price_minor = offer.price_minor if offer else 0

        now = starts_at or utcnow()
        sub = PremiumSubscription(
            id=uuid.uuid4(),
            owner_type=owner_type,
            user_id=acting_user_id,
            startup_id=startup_id,
            organization_id=organization_id,
            plan_code=plan_code,
            duration_code=duration_code,
            price_minor_at_purchase=price_minor,
            status=SUB_STATUS_ACTIVE,
            source=source,
            is_test=is_test,
            test_reason=test_reason,
            granted_by_telegram_id=granted_by_telegram_id,
            idempotency_key=idempotency_key,
            starts_at=now,
            expires_at=now + timedelta(hours=DURATION_HOURS[duration_code]),
        )
        self._session.add(sub)
        await self._session.flush()
        self._session.add(
            PremiumEvent(
                id=uuid.uuid4(),
                subscription_id=sub.id,
                event_type="granted",
                actor_user_id=acting_user_id,
                actor_telegram_id=granted_by_telegram_id,
                reason=test_reason,
                safe_payload={
                    "plan": plan_code,
                    "duration": duration_code,
                    "is_test": is_test,
                    "source": source,
                },
            )
        )
        await self._session.flush()
        return sub, True

    async def extend(
        self,
        subscription: PremiumSubscription,
        duration_code: str,
        *,
        acting_user_id: uuid.UUID | None = None,
        actor_telegram_id: int | None = None,
        reason: str | None = None,
    ) -> PremiumSubscription:
        """Add hours to an active subscription in place (history via events)."""
        if not is_known_duration(duration_code):
            raise PremiumServiceError("Неизвестный срок подписки.", code="unknown_duration")
        expires = ensure_utc(subscription.expires_at)
        if subscription.status != SUB_STATUS_ACTIVE or expires is None or expires <= utcnow():
            raise PremiumServiceError(
                "Подписка уже истекла. Оформите новую.", code="expired"
            )
        if (
            acting_user_id is not None
            and subscription.user_id != acting_user_id
            and subscription.owner_type == OWNER_USER
        ):
            raise PremiumServiceError("Нельзя продлить чужую подписку.", code="owner_mismatch")
        subscription.expires_at = subscription.expires_at + timedelta(
            hours=DURATION_HOURS[duration_code]
        )
        self._session.add(
            PremiumEvent(
                id=uuid.uuid4(),
                subscription_id=subscription.id,
                event_type="extended",
                actor_user_id=acting_user_id,
                actor_telegram_id=actor_telegram_id,
                reason=reason,
                safe_payload={"added_duration": duration_code},
            )
        )
        await self._session.flush()
        return subscription

    async def cancel(
        self,
        subscription: PremiumSubscription,
        *,
        acting_user_id: uuid.UUID | None = None,
        actor_telegram_id: int | None = None,
        reason: str | None = None,
    ) -> PremiumSubscription:
        if (
            acting_user_id is not None
            and subscription.user_id != acting_user_id
            and subscription.owner_type == OWNER_USER
        ):
            raise PremiumServiceError("Нельзя отменить чужую подписку.", code="owner_mismatch")
        subscription.status = SUB_STATUS_CANCELLED
        self._session.add(
            PremiumEvent(
                id=uuid.uuid4(),
                subscription_id=subscription.id,
                event_type="cancelled",
                actor_user_id=acting_user_id,
                actor_telegram_id=actor_telegram_id,
                reason=reason,
                safe_payload={},
            )
        )
        await self._session.flush()
        return subscription

    async def expire_overdue(self, now: datetime | None = None) -> int:
        """
        Sweep: mark overdue 'active' rows as 'expired' and log events.

        Handles bot downtime: every sensitive action also re-checks
        expires_at, so a missed sweep never grants extra access.
        """
        current = now or utcnow()
        overdue_ids = (
            await self._session.scalars(
                select(PremiumSubscription.id).where(
                    PremiumSubscription.status == SUB_STATUS_ACTIVE,
                    PremiumSubscription.expires_at <= current,
                )
            )
        ).all()
        if not overdue_ids:
            return 0
        await self._session.execute(
            update(PremiumSubscription)
            .where(PremiumSubscription.id.in_(overdue_ids))
            .values(status=SUB_STATUS_EXPIRED)
        )
        for sub_id in overdue_ids:
            self._session.add(
                PremiumEvent(
                    id=uuid.uuid4(),
                    subscription_id=sub_id,
                    event_type="expired",
                    reason=None,
                    safe_payload={},
                )
            )
        await self._session.flush()
        return len(overdue_ids)

    async def events_for(self, subscription_id: uuid.UUID) -> list[PremiumEvent]:
        result = await self._session.scalars(
            select(PremiumEvent)
            .where(PremiumEvent.subscription_id == subscription_id)
            .order_by(PremiumEvent.created_at)
        )
        return list(result.all())

    # ------------------------------------------------------- photo entitlement

    async def startup_photo_cap(self, startup: Startup) -> int:
        """Issue #5 cap: FREE=1, Startup PRO or grandfathered legacy gallery=3."""
        from raundo.domain import FREE_PHOTO_CAP, PRO_PHOTO_CAP  # local: no cycle

        if startup.grandfathered_gallery:
            return PRO_PHOTO_CAP
        if await self.has_active_startup_pro(startup.id):
            return PRO_PHOTO_CAP
        return FREE_PHOTO_CAP

    # ------------------------------------------------------------- org helpers

    async def is_approved_member(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        membership = (
            await self._session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationMembership.user_id == user_id,
                )
            )
        ).first()
        return membership is not None and membership.status == MEMBERSHIP_APPROVED

    async def active_org_subscription_for_member(
        self, user_id: uuid.UUID
    ) -> Optional[PremiumSubscription]:
        """Active org subscription where this human is an *approved* member."""
        result = await self._session.execute(
            select(PremiumSubscription, OrganizationMembership.status)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == PremiumSubscription.organization_id,
            )
            .where(
                PremiumSubscription.plan_code == PLAN_ORGANIZATION_PRO,
                _active_filter(),
                OrganizationMembership.user_id == user_id,
            )
        )
        for sub, membership_status in result.all():
            if membership_status == MEMBERSHIP_APPROVED:
                return sub
        return None

    async def seed_offers(self) -> int:
        """Insert missing catalog offers (create-only; tests and migration hook)."""
        return await ensure_premium_offers_seeded(self._session)

    async def _resolve_offer(
        self, plan_code: str, duration_code: str
    ) -> PremiumPlanOffer | None:
        return (
            await self._session.scalars(
                select(PremiumPlanOffer).where(
                    PremiumPlanOffer.plan_code == plan_code,
                    PremiumPlanOffer.duration_code == duration_code,
                    PremiumPlanOffer.is_active.is_(True),
                )
            )
        ).first()


def _ensure_same_request(
    existing: PremiumSubscription,
    *,
    plan_code: str,
    duration_code: str,
    acting_user_id: uuid.UUID,
    startup_id: uuid.UUID | None,
    organization_id: uuid.UUID | None,
) -> None:
    if (
        existing.plan_code != plan_code
        or existing.duration_code != duration_code
        or existing.user_id != acting_user_id
        or existing.startup_id != startup_id
        or existing.organization_id != organization_id
    ):
        raise PremiumServiceError(
            "Idempotency-ключ уже использован с другими параметрами.",
            code="idempotency_conflict",
        )


def plan_owner_type(plan_code: str) -> str:
    """Public owner binding for a plan; raises on unknown plans."""
    if not is_known_plan(plan_code):
        raise PremiumServiceError("Неизвестный тариф.", code="unknown_plan")
    return PLAN_OWNER_TYPE[plan_code]
