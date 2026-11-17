"""Admin directory: user search, profile 360 and access restrictions.

Everything here is server-side. The browser only receives whitelisted fields, and every
state-changing operation is audited and announced through the outbox so the user actually
learns about a restriction in Telegram.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.admin_panel import (
    AdminAccount,
    AdminAuditLog,
    Complaint,
    SupportTicket,
    UserRestriction,
)
from raundo.models.billing import PremiumSubscription
from raundo.models.investor import InvestorProfile, InvestorReview
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupReview
from raundo.models.user import User
from raundo.services.notifications import NotificationService

PROFILE_TYPES = ("founder", "investor", "representative", "viewer")
SORT_FIELDS = {"created_at", "last_seen_at", "name"}
MAX_PAGE_SIZE = 100
_RESTRICT_KIND = "blocked"
_REASON_MIN = 8


class DirectoryError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) < _REASON_MIN:
        raise DirectoryError(
            "Укажите причину не короче 8 символов.", code="reason_required"
        )
    return text[:2000]


def _audit(session: AsyncSession, **kwargs: Any) -> None:
    session.add(AdminAuditLog(id=uuid.uuid4(), **kwargs))


def _profile_conditions(kind: str):  # noqa: ANN202
    if kind == "investor":
        return (
            select(InvestorProfile.id)
            .where(
                InvestorProfile.user_id == User.id,
                InvestorProfile.deleted_at.is_(None),
            )
            .exists()
        )
    if kind == "founder":
        return (
            select(Startup.id)
            .where(Startup.owner_user_id == User.id, Startup.deleted_at.is_(None))
            .exists()
        )
    if kind == "representative":
        return (
            select(OrganizationMembership.id)
            .where(OrganizationMembership.user_id == User.id)
            .exists()
        )
    # viewer: none of the known profile types
    return ~or_(
        select(InvestorProfile.id)
        .where(
            InvestorProfile.user_id == User.id,
            InvestorProfile.deleted_at.is_(None),
        )
        .exists(),
        select(Startup.id)
        .where(Startup.owner_user_id == User.id, Startup.deleted_at.is_(None))
        .exists(),
        select(OrganizationMembership.id)
        .where(OrganizationMembership.user_id == User.id)
        .exists(),
    )


async def list_users(
    session: AsyncSession,
    *,
    q: str | None = None,
    status: str | None = None,
    profile_type: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict:
    page = max(1, page)
    page_size = min(MAX_PAGE_SIZE, max(1, page_size))
    conditions = []
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        matches = [
            User.username.ilike(like),
            User.first_name.ilike(like),
            User.last_name.ilike(like),
            select(Startup.id)
            .where(
                Startup.owner_user_id == User.id,
                Startup.deleted_at.is_(None),
                Startup.name.ilike(like),
            )
            .exists(),
        ]
        if needle.isdigit():
            matches.append(User.telegram_id == int(needle))
        conditions.append(or_(*matches))
    if status:
        conditions.append(User.status == status)
    if profile_type:
        if profile_type not in PROFILE_TYPES:
            raise DirectoryError("Неизвестный тип профиля.", code="profile_type")
        conditions.append(_profile_conditions(profile_type))

    total = await session.scalar(
        select(func.count()).select_from(User).where(*conditions)
    )
    sort_column = {
        "created_at": User.created_at,
        "last_seen_at": User.last_seen_at,
        "name": User.first_name,
    }[sort if sort in SORT_FIELDS else "created_at"]
    order = sort_column.asc() if direction == "asc" else sort_column.desc()
    rows = await session.execute(
        select(User)
        .where(*conditions)
        .order_by(order, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = list(rows.scalars())
    items = await _enrich_users(session, users)
    return {
        "items": items,
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "pages": max(1, (int(total or 0) + page_size - 1) // page_size),
        "profile_types": list(PROFILE_TYPES),
    }


async def _enrich_users(session: AsyncSession, users: list[User]) -> list[dict]:
    if not users:
        return []
    ids = [u.id for u in users]
    founders = dict(
        (
            await session.execute(
                select(Startup.owner_user_id, func.count())
                .where(Startup.owner_user_id.in_(ids), Startup.deleted_at.is_(None))
                .group_by(Startup.owner_user_id)
            )
        ).all()
    )
    investors = set(
        (
            await session.execute(
                select(InvestorProfile.user_id).where(
                    InvestorProfile.user_id.in_(ids),
                    InvestorProfile.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    representatives = set(
        (
            await session.execute(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.user_id.in_(ids)
                )
            )
        ).scalars()
    )
    premium_rows = (
        await session.execute(
            select(PremiumSubscription.user_id, PremiumSubscription.plan_code).where(
                PremiumSubscription.user_id.in_(ids),
                PremiumSubscription.status == "active",
                PremiumSubscription.owner_type == "user",
            )
        )
    ).all()
    premium = {user_id: plan for user_id, plan in premium_rows}

    items = []
    for user in users:
        types = []
        if founders.get(user.id):
            types.append("founder")
        if user.id in investors:
            types.append("investor")
        if user.id in representatives:
            types.append("representative")
        if not types:
            types.append("viewer")
        items.append(
            {
                "id": str(user.id),
                "telegram_id": user.telegram_id,
                "username": user.username,
                "name": " ".join(
                    part for part in (user.first_name, user.last_name) if part
                )
                or None,
                "profile_types": types,
                "status": user.status,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
                "premium": premium.get(user.id),
                "startups_count": int(founders.get(user.id) or 0),
            }
        )
    return items


async def user_detail(session: AsyncSession, user_id: uuid.UUID) -> dict:
    user = await session.get(User, user_id)
    if user is None:
        raise DirectoryError("Пользователь не найден.", code="missing")

    startups = list(
        (
            await session.execute(
                select(Startup)
                .where(Startup.owner_user_id == user.id)
                .order_by(Startup.updated_at.desc())
            )
        ).scalars()
    )
    investor = await session.scalar(
        select(InvestorProfile)
        .where(InvestorProfile.user_id == user.id, InvestorProfile.deleted_at.is_(None))
        .order_by(InvestorProfile.created_at)
        .limit(1)
    )
    investors = list(
        (
            await session.execute(
                select(InvestorProfile)
                .where(InvestorProfile.user_id == user.id)
                .order_by(InvestorProfile.created_at)
            )
        ).scalars()
    )
    created_orgs = list(
        (
            await session.execute(
                select(FundingOrganization)
                .where(FundingOrganization.created_by_user_id == user.id)
                .order_by(FundingOrganization.created_at)
            )
        ).scalars()
    )
    memberships = (
        await session.execute(
            select(OrganizationMembership, FundingOrganization)
            .join(
                FundingOrganization,
                FundingOrganization.id == OrganizationMembership.organization_id,
            )
            .where(OrganizationMembership.user_id == user.id)
        )
    ).all()
    tickets = list(
        (
            await session.execute(
                select(SupportTicket)
                .where(SupportTicket.user_id == user.id)
                .order_by(SupportTicket.updated_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    premium = list(
        (
            await session.execute(
                select(PremiumSubscription)
                .where(PremiumSubscription.user_id == user.id)
                .order_by(PremiumSubscription.created_at.desc())
            )
        ).scalars()
    )
    restrictions = list(
        (
            await session.execute(
                select(UserRestriction)
                .where(UserRestriction.user_id == user.id)
                .order_by(UserRestriction.created_at.desc())
            )
        ).scalars()
    )
    complaints = list(
        (
            await session.execute(
                select(Complaint)
                .where(
                    or_(
                        Complaint.reporter_user_id == user.id,
                        (Complaint.target_type == "user")
                        & (Complaint.target_id == str(user.id)),
                    )
                )
                .order_by(Complaint.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )

    history = []
    if startups:
        startup_ids = [s.id for s in startups]
        startup_names = {s.id: s.name for s in startups}
        reviews = (
            await session.execute(
                select(StartupReview)
                .where(StartupReview.startup_id.in_(startup_ids))
                .order_by(StartupReview.submitted_at.desc())
            )
        ).scalars()
        for review in reviews:
            history.append(
                {
                    "kind": "startup",
                    "title": startup_names.get(review.startup_id, "Стартап"),
                    "review_id": str(review.id),
                    "revision": review.revision,
                    "state": review.state,
                    "comment": review.comment,
                    "submitted_at": review.submitted_at.isoformat()
                    if review.submitted_at
                    else None,
                    "decided_at": review.decided_at.isoformat()
                    if review.decided_at
                    else None,
                }
            )
    if investor is not None:
        reviews = (
            await session.execute(
                select(InvestorReview)
                .where(InvestorReview.investor_id == investor.id)
                .order_by(InvestorReview.created_at.desc())
            )
        ).scalars()
        for review in reviews:
            history.append(
                {
                    "kind": "investor",
                    "title": investor.display_name or "Инвестор",
                    "review_id": str(review.id),
                    "revision": review.revision,
                    "state": review.state,
                    "comment": review.decision_reason,
                    "submitted_at": review.created_at.isoformat()
                    if review.created_at
                    else None,
                    "decided_at": review.decided_at.isoformat()
                    if review.decided_at
                    else None,
                }
            )
    history.sort(key=lambda item: item["submitted_at"] or "", reverse=True)

    actions = (
        await session.execute(
            select(AdminAuditLog, AdminAccount)
            .outerjoin(AdminAccount, AdminAccount.id == AdminAuditLog.actor_id)
            .where(
                AdminAuditLog.resource_type == "user",
                AdminAuditLog.resource_id == str(user.id),
            )
            .order_by(AdminAuditLog.created_at.desc())
            .limit(50)
        )
    ).all()

    from raundo.premium_catalog import (
        PLAN_INVESTOR_PRO,
        PLAN_ORGANIZATION_PRO,
        PLAN_STARTUP_PRO,
    )
    from raundo.services.premium import PremiumService
    from raundo.services.profile_limits import limits_for_user

    premium_service = PremiumService(session)

    async def _until(plan_code: str, **kwargs) -> str | None:  # noqa: ANN003
        sub = await premium_service.active_subscription(plan_code, **kwargs)
        return sub.expires_at.isoformat() if sub and sub.expires_at else None

    startup_premium: dict[str, str | None] = {}
    for startup in startups:
        startup_premium[str(startup.id)] = await _until(
            PLAN_STARTUP_PRO, startup_id=startup.id
        )
    investor_premium = await _until(PLAN_INVESTOR_PRO, user_id=user.id)
    org_premium: dict[str, str | None] = {}
    for org in created_orgs:
        org_premium[str(org.id)] = await _until(
            PLAN_ORGANIZATION_PRO, organization_id=org.id
        )

    return {
        "id": str(user.id),
        "telegram_id": user.telegram_id,
        "username": user.username,
        "name": " ".join(part for part in (user.first_name, user.last_name) if part)
        or None,
        "profile_limits": (await limits_for_user(session, user.id)).as_dict(),
        "profile_types": [
            *(["founder"] if startups else []),
            *(["investor"] if investor is not None else []),
            *(["representative"] if memberships else []),
            *([] if startups or investor or memberships else ["viewer"]),
        ],
        "status": user.status,
        "locale": user.locale,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "startups": [
            {
                "id": str(s.id),
                "name": s.name,
                "review_state": s.review_state,
                "revision": s.current_revision,
                "published_at": s.published_at.isoformat() if s.published_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "archived": s.deleted_at is not None,
                "premium_until": startup_premium.get(str(s.id)),
            }
            for s in startups
        ],
        "investors": [
            {
                "id": str(p.id),
                "display_name": p.display_name,
                "review_status": p.review_status,
                "investor_type": p.investor_type,
                "current_revision": p.current_revision,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "archived": p.deleted_at is not None,
                "premium_until": investor_premium,
            }
            for p in investors
        ],
        "organizations_created": [
            {
                "id": str(o.id),
                "name": o.name,
                "review_status": o.review_status,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "premium_until": org_premium.get(str(o.id)),
            }
            for o in created_orgs
        ],
        "investor": {
            "id": str(investor.id),
            "display_name": investor.display_name,
            "review_status": investor.review_status,
            "investor_type": investor.investor_type,
            "current_revision": investor.current_revision,
        }
        if investor
        else None,
        "memberships": [
            {
                "membership_id": str(m.id),
                "organization_id": str(o.id),
                "organization": o.name,
                "organization_status": o.review_status,
                "status": m.status,
                "mandate": bool(m.claims_direct_investment_mandate),
            }
            for m, o in memberships
        ],
        "tickets": [
            {
                "id": str(t.id),
                "number": t.public_number,
                "subject": t.subject,
                "status": t.status,
            }
            for t in tickets
        ],
        "premium": [
            {
                "id": str(p.id),
                "plan": p.plan_code,
                "status": p.status,
                "owner_type": p.owner_type,
                "starts_at": p.starts_at.isoformat() if p.starts_at else None,
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in premium
        ],
        "restrictions": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "reason": r.reason,
                "active": bool(r.active),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in restrictions
        ],
        "complaints": [
            {
                "id": str(c.id),
                "number": c.public_number,
                "target_type": c.target_type,
                "status": c.status,
                "reason": c.reason,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in complaints
        ],
        "moderation_history": history,
        "admin_actions": [
            {
                "action": log.action,
                "actor": actor.login if actor else None,
                "reason": log.reason,
                "created_at": log.created_at.isoformat(),
            }
            for log, actor in actions
        ],
    }


async def _active_restriction(
    session: AsyncSession, user_id: uuid.UUID
) -> UserRestriction | None:
    return await session.scalar(
        select(UserRestriction).where(
            UserRestriction.user_id == user_id, UserRestriction.active.is_(True)
        )
    )


async def restrict_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    reason: str,
) -> UserRestriction:
    text = _reason(reason)
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise DirectoryError("Пользователь не найден.", code="missing")
    if await _active_restriction(session, user_id) is not None:
        raise DirectoryError("Доступ уже ограничен.", code="already_restricted")
    before_status = user.status
    restriction = UserRestriction(
        id=uuid.uuid4(),
        user_id=user.id,
        kind=_RESTRICT_KIND,
        reason=text,
        active=True,
        created_by=actor_id,
    )
    session.add(restriction)
    user.status = "restricted"
    _audit(
        session,
        actor_id=actor_id,
        action="restrict",
        resource_type="user",
        resource_id=str(user.id),
        before_state={"status": before_status},
        after_state={"status": user.status},
        reason=text,
    )
    await NotificationService(session).enqueue(
        kind="user.restricted",
        dedupe_key=f"user_restricted:{restriction.id}",
        recipient_user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
        safe_payload={"reason": text},
    )
    await session.flush()
    return restriction


async def unrestrict_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    reason: str,
) -> int:
    text = _reason(reason)
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise DirectoryError("Пользователь не найден.", code="missing")
    rows = (
        await session.execute(
            select(UserRestriction).where(
                UserRestriction.user_id == user.id,
                UserRestriction.active.is_(True),
            )
        )
    ).scalars()
    released = 0
    for restriction in rows:
        restriction.active = False
        released += 1
    if released == 0:
        raise DirectoryError("Активных ограничений нет.", code="not_restricted")
    before_status = user.status
    user.status = "active"
    _audit(
        session,
        actor_id=actor_id,
        action="unrestrict",
        resource_type="user",
        resource_id=str(user.id),
        before_state={"status": before_status, "active_restrictions": released},
        after_state={"status": user.status, "active_restrictions": 0},
        reason=text,
    )
    await NotificationService(session).enqueue(
        kind="user.unrestricted",
        dedupe_key=f"user_unrestricted:{user.id}:{datetime.now(timezone.utc).timestamp()}",
        recipient_user_id=user.id,
        resource_type="user",
        resource_id=str(user.id),
        safe_payload={"reason": text},
    )
    await session.flush()
    return released


__all__ = [
    "DirectoryError",
    "PROFILE_TYPES",
    "list_users",
    "restrict_user",
    "unrestrict_user",
    "user_detail",
]
