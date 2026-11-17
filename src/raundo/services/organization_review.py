"""Manual review of funding organizations, representatives and investment mandates.

A verified organization is not a verified employee and not guaranteed capital, so the two
decisions are separate and both are audited. Only an approved membership that claims a direct
investment mandate can later qualify for investor-only placement.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import REVIEW_APPROVED, REVIEW_CHANGES, REVIEW_REJECTED
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.user import User
from raundo.participants import SUPPORT_MODE_LABELS, participant_type_label
from raundo.services.consents import has_publication_consent
from raundo.services.notifications import NotificationService
from raundo.services.organizations import (
    MEMBERSHIP_APPROVED,
    MEMBERSHIP_REVOKED,
    MEMBERSHIP_UNDER_REVIEW,
    OrganizationServiceError,
    normalize_org_name,
)

ORG_OPEN_STATES = ("draft", "submitted", "under_review")
MEMBERSHIP_OPEN_STATES = ("claimed", "under_review")
MEMBERSHIP_REJECTED = "rejected"
_REASON_MIN = 8


class OrganizationReviewError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def _audit(session: AsyncSession, **kwargs) -> None:  # noqa: ANN003
    from raundo.models.admin_panel import AdminAuditLog

    session.add(AdminAuditLog(id=uuid.uuid4(), **kwargs))


def _require_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if len(text) < _REASON_MIN:
        raise OrganizationReviewError("Укажите причину не короче 8 символов.", code="reason_required")
    return text


def can_verify_membership(org: FundingOrganization) -> bool:
    return org.review_status == REVIEW_APPROVED


async def list_organization_queue(session: AsyncSession, limit: int = 100) -> list[dict]:
    pending = (
        select(
            OrganizationMembership.organization_id.label("org_id"),
            func.count().label("pending"),
        )
        .where(OrganizationMembership.status.in_(MEMBERSHIP_OPEN_STATES))
        .group_by(OrganizationMembership.organization_id)
        .subquery()
    )
    rows = await session.execute(
        select(FundingOrganization, func.coalesce(pending.c.pending, 0))
        .outerjoin(pending, pending.c.org_id == FundingOrganization.id)
        .order_by(FundingOrganization.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(org.id),
            "name": org.name,
            "participant_type": org.participant_type,
            "country": org.country,
            "website_url": org.website_url,
            "review_status": org.review_status,
            "pending_representatives": int(count),
        }
        for org, count in rows.all()
    ]


async def organization_detail(session: AsyncSession, org_id: uuid.UUID) -> dict:
    org = await session.get(FundingOrganization, org_id)
    if org is None:
        raise OrganizationReviewError("Организация не найдена.", code="missing")
    rows = await session.execute(
        select(OrganizationMembership, User)
        .outerjoin(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == org.id)
        .order_by(OrganizationMembership.created_at)
    )
    memberships = []
    for membership, user in rows.all():
        memberships.append(
            {
                "id": str(membership.id),
                "role": membership.role,
                "status": membership.status,
                "claims_direct_investment_mandate": bool(
                    membership.claims_direct_investment_mandate
                ),
                "verified_at": membership.verified_at.isoformat()
                if membership.verified_at
                else None,
                "decision_reason": membership.decision_reason,
                "user": {
                    "id": str(user.id) if user else None,
                    "telegram_id": user.telegram_id if user else None,
                    "username": user.username if user else None,
                    "name": user.first_name if user else None,
                },
            }
        )
    return {
        "id": str(org.id),
        "name": org.name,
        "participant_type": org.participant_type,
        "participant_type_label": participant_type_label(org.participant_type),
        "website_url": org.website_url,
        "country": org.country,
        "description": org.description,
        "support_modes": list(org.support_modes or []),
        "support_mode_labels": [
            SUPPORT_MODE_LABELS.get(code, "") for code in (org.support_modes or []) if code in SUPPORT_MODE_LABELS
        ],
        "open_for_requests": bool(org.open_for_requests),
        "review_status": org.review_status,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "memberships": memberships,
        "can_verify_memberships": can_verify_membership(org),
    }


async def review_organization(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    decision: str,
    reason: str | None = None,
    official_name: str | None = None,
) -> FundingOrganization:
    org = await session.get(FundingOrganization, org_id, with_for_update=True)
    if org is None:
        raise OrganizationReviewError("Организация не найдена.", code="missing")
    if decision not in {"approve", "reject", "changes"}:
        raise OrganizationReviewError("Неизвестное решение.", code="decision")
    if org.review_status == REVIEW_APPROVED and decision == "approve":
        raise OrganizationReviewError("Организация уже подтверждена.", code="not_open")
    if decision != "approve":
        _require_reason(reason)

    before = org.review_status
    if official_name and official_name.strip():
        org.name = official_name.strip()[:256]
        org.normalized_name = normalize_org_name(org.name)

    if decision == "approve":
        if org.created_by_user_id is not None and not await has_publication_consent(
            session, org.created_by_user_id
        ):
            raise OrganizationReviewError(
                "Автор организации не дал согласие на публикацию данных.",
                code="consent",
            )
        org.review_status = REVIEW_APPROVED
        kind = "organization.approved"
        payload = {"name": org.name}
        action = "approve"
    elif decision == "reject":
        org.review_status = REVIEW_REJECTED
        kind = "organization.rejected"
        payload = {"name": org.name, "reason": _require_reason(reason)}
        action = "reject"
    else:
        org.review_status = REVIEW_CHANGES
        kind = "organization.changes_requested"
        payload = {"name": org.name, "comment": _require_reason(reason)}
        action = "request_changes"

    if org.created_by_user_id is not None:
        await NotificationService(session).enqueue(
            kind=kind,
            dedupe_key=f"{kind}:{org.id}:{org.review_status}",
            recipient_user_id=org.created_by_user_id,
            resource_type="organization",
            resource_id=str(org.id),
            safe_payload=payload,
        )
    _audit(
        session,
        actor_id=actor_id,
        action=action,
        resource_type="organization",
        resource_id=str(org.id),
        before_state={"review_status": before},
        after_state={"review_status": org.review_status},
        reason=(reason or "").strip() or None,
    )
    await session.flush()
    return org


async def review_membership(
    session: AsyncSession,
    *,
    membership_id: uuid.UUID,
    actor_id: uuid.UUID,
    decision: str,
    reason: str | None = None,
    mandate: bool | None = None,
) -> OrganizationMembership:
    membership = await session.get(
        OrganizationMembership, membership_id, with_for_update=True
    )
    if membership is None:
        raise OrganizationReviewError("Представитель не найден.", code="missing")
    org = await session.get(FundingOrganization, membership.organization_id)
    if org is None:
        raise OrganizationReviewError("Организация не найдена.", code="missing")
    if decision not in {"approve", "reject", "revoke"}:
        raise OrganizationReviewError("Неизвестное решение.", code="decision")
    if membership.status == MEMBERSHIP_REVOKED:
        raise OrganizationReviewError("Полномочия уже отозваны.", code="not_open")
    if decision == "approve":
        if membership.status not in MEMBERSHIP_OPEN_STATES:
            raise OrganizationReviewError("Заявка уже рассмотрена.", code="not_open")
        if not can_verify_membership(org):
            raise OrganizationReviewError(
                "Сначала подтвердите организацию.", code="organization_not_approved"
            )
    else:
        _require_reason(reason)

    before = {
        "status": membership.status,
        "mandate": bool(membership.claims_direct_investment_mandate),
    }
    now = datetime.now(timezone.utc)
    if decision == "approve":
        membership.status = MEMBERSHIP_APPROVED
        membership.verified_by = actor_id
        membership.verified_at = now
        membership.claims_direct_investment_mandate = (
            bool(membership.claims_direct_investment_mandate) if mandate is None else bool(mandate)
        )
        membership.decision_reason = (reason or "").strip() or None
        kind = "membership.approved"
        action = "approve"
        payload = {
            "name": org.name,
            "mandate": membership.claims_direct_investment_mandate,
        }
    elif decision == "reject":
        membership.status = MEMBERSHIP_REJECTED
        membership.decision_reason = _require_reason(reason)
        kind = "membership.rejected"
        action = "reject"
        payload = {"name": org.name, "reason": membership.decision_reason}
    else:
        membership.status = MEMBERSHIP_REVOKED
        membership.claims_direct_investment_mandate = False
        membership.revoked_at = now
        membership.decision_reason = _require_reason(reason)
        kind = "membership.revoked"
        action = "revoke"
        payload = {"name": org.name, "reason": membership.decision_reason}

    await NotificationService(session).enqueue(
        kind=kind,
        dedupe_key=f"{kind}:{membership.id}:{membership.status}",
        recipient_user_id=membership.user_id,
        resource_type="membership",
        resource_id=str(membership.id),
        safe_payload=payload,
    )
    _audit(
        session,
        actor_id=actor_id,
        action=action,
        resource_type="membership",
        resource_id=str(membership.id),
        before_state=before,
        after_state={
            "status": membership.status,
            "mandate": bool(membership.claims_direct_investment_mandate),
        },
        reason=(reason or "").strip() or None,
    )
    await session.flush()
    return membership


async def mark_membership_under_review(
    session: AsyncSession, *, membership_id: uuid.UUID
) -> OrganizationMembership:
    membership = await session.get(OrganizationMembership, membership_id)
    if membership is None:
        raise OrganizationReviewError("Представитель не найден.", code="missing")
    if membership.status == "claimed":
        membership.status = MEMBERSHIP_UNDER_REVIEW
        await session.flush()
    return membership


__all__ = [
    "MEMBERSHIP_OPEN_STATES",
    "OrganizationReviewError",
    "OrganizationServiceError",
    "can_verify_membership",
    "list_organization_queue",
    "mark_membership_under_review",
    "organization_detail",
    "review_membership",
    "review_organization",
]
