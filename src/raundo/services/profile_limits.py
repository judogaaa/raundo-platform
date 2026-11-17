"""Profile quotas: 1 free profile per type, extra slots need the matching PRO plan.

The number of extra slots is intentionally configurable: the current product
specification does not freeze it yet (see docs/DECISIONS.md). Defaults are
provisional and never unlimited.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization
from raundo.models.startup import Startup
from raundo.premium_catalog import (
    PLAN_INVESTOR_PRO,
    PLAN_ORGANIZATION_PRO,
    PLAN_STARTUP_PRO,
)
from raundo.services.premium import PremiumService

KIND_STARTUP = "startup"
KIND_INVESTOR = "investor"
KIND_ORGANIZATION = "organization"

PLAN_FOR_KIND = {
    KIND_STARTUP: PLAN_STARTUP_PRO,
    KIND_INVESTOR: PLAN_INVESTOR_PRO,
    KIND_ORGANIZATION: PLAN_ORGANIZATION_PRO,
}


class ProfileLimitError(ValueError):
    def __init__(self, message: str, *, code: str, kind: str) -> None:
        self.code = code
        self.kind = kind
        super().__init__(message)


def _env_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def free_limit() -> int:
    return _env_limit("RAUNDO_PROFILE_LIMIT_FREE", 1)


def pro_limit() -> int:
    # Owner-approved rule (2026-11-02): a matching PRO plan allows up to 3
    # profiles of that type in total, including the free one.
    return _env_limit("RAUNDO_PROFILE_LIMIT_PRO", 3)


@dataclass
class KindLimit:
    kind: str
    used: int
    limit: int
    plan_code: str | None = None
    plan_until: str | None = None

    @property
    def available(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def is_pro(self) -> bool:
        return self.plan_code is not None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "used": self.used,
            "limit": self.limit,
            "available": self.available,
            "plan_code": self.plan_code,
            "plan_until": self.plan_until,
        }


@dataclass
class ProfileLimits:
    startup: KindLimit
    investor: KindLimit
    organization: KindLimit
    plans: dict[str, str] = field(default_factory=dict)

    def get(self, kind: str) -> KindLimit:
        return getattr(self, kind)

    def as_dict(self) -> dict:
        return {
            "startup": self.startup.as_dict(),
            "investor": self.investor.as_dict(),
            "organization": self.organization.as_dict(),
            "plans": self.plans,
        }


async def _used_counts(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, int, int]:
    # Soft-deleted rows are excluded so the owner can start a fresh profile,
    # but restoring one is quota-checked separately (revive path).
    startups = await session.scalar(
        select(func.count())
        .select_from(Startup)
        .where(Startup.owner_user_id == user_id, Startup.deleted_at.is_(None))
    )
    investors = await session.scalar(
        select(func.count())
        .select_from(InvestorProfile)
        .where(
            InvestorProfile.user_id == user_id,
            InvestorProfile.deleted_at.is_(None),
        )
    )
    organizations = await session.scalar(
        select(func.count())
        .select_from(FundingOrganization)
        .where(FundingOrganization.created_by_user_id == user_id)
    )
    return int(startups or 0), int(investors or 0), int(organizations or 0)


async def limits_for_user(session: AsyncSession, user_id: uuid.UUID) -> ProfileLimits:
    used_startup, used_investor, used_org = await _used_counts(session, user_id)
    active = await PremiumService(session).user_entitlements(user_id)
    free = free_limit()
    pro = pro_limit()
    base = free

    def kind_limit(kind: str, used: int) -> KindLimit:
        plan = active.get(PLAN_FOR_KIND[kind])
        if plan is None:
            return KindLimit(kind=kind, used=used, limit=max(base, used) if used > base else base)
        until = plan.expires_at.isoformat() if plan.expires_at else None
        return KindLimit(
            kind=kind,
            used=used,
            limit=max(pro, used) if used > pro else pro,
            plan_code=plan.plan_code,
            plan_until=until,
        )

    return ProfileLimits(
        startup=kind_limit(KIND_STARTUP, used_startup),
        investor=kind_limit(KIND_INVESTOR, used_investor),
        organization=kind_limit(KIND_ORGANIZATION, used_org),
        plans={code: sub.expires_at.isoformat() for code, sub in active.items() if sub.expires_at},
    )


async def _lock(session: AsyncSession, user_id: uuid.UUID, kind: str) -> None:
    """Serialize quota check+create per user/kind on PostgreSQL."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    key = f"profile_quota:{user_id}:{kind}"
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


async def ensure_can_create(
    session: AsyncSession, *, user_id: uuid.UUID, kind: str
) -> ProfileLimits:
    """Raise ProfileLimitError when the user has no free slot left for this kind."""
    if kind not in PLAN_FOR_KIND:
        raise ValueError(f"unknown profile kind {kind!r}")
    await _lock(session, user_id, kind)
    limits = await limits_for_user(session, user_id)
    limit = limits.get(kind)
    if limit.used >= limit.limit:
        plan_title = {
            KIND_STARTUP: "Startup PRO",
            KIND_INVESTOR: "Investor PRO",
            KIND_ORGANIZATION: "Organization PRO",
        }[kind]
        raise ProfileLimitError(
            "На бесплатном тарифе можно создать один профиль этого типа. "
            f"Для дополнительного потребуется {plan_title}.",
            code="limit_reached",
            kind=kind,
        )
    return limits
