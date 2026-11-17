"""Admin moderation decisions shared by the web API. Uses review_guards."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import (
    REVIEW_APPROVED,
    REVIEW_CHANGES,
    REVIEW_REJECTED,
    REVIEW_SUSPENDED,
)
from raundo.models.investor import InvestorProfile, InvestorReview
from raundo.models.startup import Startup, StartupReview
from raundo.services.consents import has_publication_consent
from raundo.services.notifications import NotificationService
from raundo.services.review_guards import (
    ReviewDecisionError,
    validate_investor_review_decision,
    validate_startup_review_decision,
)


async def _audit(session: AsyncSession, **kwargs) -> None:
    from raundo.models.admin_panel import AdminAuditLog

    session.add(AdminAuditLog(id=uuid.uuid4(), **kwargs))


async def approve_startup(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID
) -> Startup:
    review = await session.get(StartupReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    startup = await session.get(Startup, review.startup_id, with_for_update=True)
    if startup is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # The entity lock was just acquired: re-read the review so a concurrent
    # decision by another staff member cannot be applied twice.
    await session.refresh(review)
    validate_startup_review_decision(startup, review)
    if not await has_publication_consent(session, startup.owner_user_id):
        raise ReviewDecisionError(
            "Автор анкеты не дал согласие на публикацию данных.",
            code="consent",
        )
    before = startup.review_state
    now = datetime.now(timezone.utc)
    review.state = REVIEW_APPROVED
    review.decided_at = now
    review.reviewer_id = actor_id
    startup.review_state = REVIEW_APPROVED
    startup.published_snapshot = None
    startup.published_at = now
    await NotificationService(session).enqueue(
        kind="startup.approved",
        dedupe_key=f"startup_approved:{startup.id}:{startup.current_revision}",
        recipient_user_id=startup.owner_user_id,
        resource_type="startup",
        resource_id=str(startup.id),
        safe_payload={"name": startup.name, "revision": startup.current_revision},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="approve",
        resource_type="startup",
        resource_id=str(startup.id),
        before_state={"review_state": before},
        after_state={"review_state": REVIEW_APPROVED, "revision": startup.current_revision},
        reason=None,
    )
    await session.flush()
    return startup


async def request_startup_changes(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID, comment: str
) -> Startup:
    if len(comment.strip()) < 8:
        raise ReviewDecisionError("Укажите замечания.", code="reason_required")
    review = await session.get(StartupReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    startup = await session.get(Startup, review.startup_id, with_for_update=True)
    if startup is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # The entity lock was just acquired: re-read the review so a concurrent
    # decision by another staff member cannot be applied twice.
    await session.refresh(review)
    validate_startup_review_decision(startup, review)
    now = datetime.now(timezone.utc)
    review.state = REVIEW_CHANGES
    review.comment = comment.strip()
    review.decided_at = now
    review.reviewer_id = actor_id
    startup.review_state = REVIEW_CHANGES
    await NotificationService(session).enqueue(
        kind="startup.changes_requested",
        dedupe_key=f"startup_changes:{startup.id}:{startup.current_revision}",
        recipient_user_id=startup.owner_user_id,
        resource_type="startup",
        resource_id=str(startup.id),
        safe_payload={"name": startup.name, "comment": comment.strip()},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="request_changes",
        resource_type="startup",
        resource_id=str(startup.id),
        before_state=None,
        after_state={"review_state": REVIEW_CHANGES},
        reason=comment.strip(),
    )
    await session.flush()
    return startup


async def reject_startup(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID, reason: str
) -> Startup:
    if len(reason.strip()) < 8:
        raise ReviewDecisionError("Укажите причину отклонения.", code="reason_required")
    review = await session.get(StartupReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    startup = await session.get(Startup, review.startup_id, with_for_update=True)
    if startup is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # The entity lock was just acquired: re-read the review so a concurrent
    # decision by another staff member cannot be applied twice.
    await session.refresh(review)
    validate_startup_review_decision(startup, review)
    now = datetime.now(timezone.utc)
    review.state = REVIEW_REJECTED
    review.comment = reason.strip()
    review.decided_at = now
    review.reviewer_id = actor_id
    startup.review_state = REVIEW_REJECTED
    startup.published_snapshot = None
    await NotificationService(session).enqueue(
        kind="startup.rejected",
        dedupe_key=f"startup_rejected:{startup.id}:{startup.current_revision}",
        recipient_user_id=startup.owner_user_id,
        resource_type="startup",
        resource_id=str(startup.id),
        safe_payload={"name": startup.name, "reason": reason.strip()},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="reject",
        resource_type="startup",
        resource_id=str(startup.id),
        before_state=None,
        after_state={"review_state": REVIEW_REJECTED},
        reason=reason.strip(),
    )
    await session.flush()
    return startup


async def approve_investor(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID
) -> InvestorProfile:
    review = await session.get(InvestorReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    profile = await session.get(InvestorProfile, review.investor_id, with_for_update=True)
    if profile is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # Same as startups: refresh the review after taking the profile lock.
    await session.refresh(review)
    validate_investor_review_decision(profile, review)
    if not await has_publication_consent(session, profile.user_id):
        raise ReviewDecisionError(
            "Автор анкеты не дал согласие на публикацию данных.",
            code="consent",
        )
    now = datetime.now(timezone.utc)
    review.state = REVIEW_APPROVED
    review.decided_at = now
    review.reviewer_user_id = actor_id
    profile.review_status = REVIEW_APPROVED
    profile.approved_at = now
    profile.approved_by = actor_id
    profile.published_snapshot = None
    await NotificationService(session).enqueue(
        kind="investor.approved",
        dedupe_key=f"investor_approved:{profile.id}:{profile.current_revision}",
        recipient_user_id=profile.user_id,
        resource_type="investor",
        resource_id=str(profile.id),
        safe_payload={"name": profile.display_name or "Анкета"},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="approve",
        resource_type="investor",
        resource_id=str(profile.id),
        before_state=None,
        after_state={"review_status": REVIEW_APPROVED},
        reason=None,
    )
    await session.flush()
    return profile


async def reject_investor(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID, reason: str
) -> InvestorProfile:
    if len(reason.strip()) < 8:
        raise ReviewDecisionError("Укажите причину отклонения.", code="reason_required")
    review = await session.get(InvestorReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    profile = await session.get(InvestorProfile, review.investor_id, with_for_update=True)
    if profile is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # Same as startups: refresh the review after taking the profile lock.
    await session.refresh(review)
    validate_investor_review_decision(profile, review)
    now = datetime.now(timezone.utc)
    review.state = REVIEW_REJECTED
    review.decision_reason = reason.strip()
    review.decided_at = now
    profile.review_status = REVIEW_REJECTED
    profile.published_snapshot = None
    await NotificationService(session).enqueue(
        kind="investor.rejected",
        dedupe_key=f"investor_rejected:{profile.id}:{profile.current_revision}",
        recipient_user_id=profile.user_id,
        resource_type="investor",
        resource_id=str(profile.id),
        safe_payload={"name": profile.display_name or "Анкета", "reason": reason.strip()},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="reject",
        resource_type="investor",
        resource_id=str(profile.id),
        before_state=None,
        after_state={"review_status": REVIEW_REJECTED},
        reason=reason.strip(),
    )
    await session.flush()
    return profile


async def request_investor_changes(
    session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID, comment: str
) -> InvestorProfile:
    if len(comment.strip()) < 8:
        raise ReviewDecisionError("Укажите замечания.", code="reason_required")
    review = await session.get(InvestorReview, review_id)
    if review is None:
        raise ReviewDecisionError("Заявка не найдена.", code="missing")
    profile = await session.get(InvestorProfile, review.investor_id, with_for_update=True)
    if profile is None:
        raise ReviewDecisionError("Анкета не найдена.", code="missing")
    # Same as startups: refresh the review after taking the profile lock.
    await session.refresh(review)
    validate_investor_review_decision(profile, review)
    now = datetime.now(timezone.utc)
    review.state = REVIEW_CHANGES
    review.decision_reason = comment.strip()
    review.decided_at = now
    profile.review_status = REVIEW_CHANGES
    await NotificationService(session).enqueue(
        kind="investor.changes_requested",
        dedupe_key=f"investor_changes:{profile.id}:{profile.current_revision}",
        recipient_user_id=profile.user_id,
        resource_type="investor",
        resource_id=str(profile.id),
        safe_payload={"name": profile.display_name or "Анкета", "comment": comment.strip()},
    )
    await _audit(
        session,
        actor_id=actor_id,
        action="request_changes",
        resource_type="investor",
        resource_id=str(profile.id),
        before_state=None,
        after_state={"review_status": REVIEW_CHANGES},
        reason=comment.strip(),
    )
    await session.flush()
    return profile


def revision_diff(published: dict | None, current: dict) -> list[dict]:
    """Field-level diff between the last published snapshot and the pending view."""
    left = published or {}
    changed = []
    keys = sorted(set(left) | set(current))
    for key in keys:
        if left.get(key) != current.get(key):
            changed.append({"field": key, "published": left.get(key), "pending": current.get(key)})
    return changed


async def list_open_startup_reviews(session: AsyncSession, limit: int = 50) -> list[dict]:
    from raundo.domain import REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW

    rows = await session.execute(
        select(StartupReview, Startup)
        .join(Startup, Startup.id == StartupReview.startup_id)
        .where(StartupReview.state.in_([REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW]))
        .order_by(StartupReview.submitted_at.desc())
        .limit(limit)
    )
    out = []
    for review, startup in rows.all():
        out.append(
            {
                "review_id": str(review.id),
                "kind": "startup",
                "title": startup.name,
                "state": review.state,
                "revision": review.revision,
                "submitted_at": review.submitted_at.isoformat(),
            }
        )
    return out


async def list_open_investor_reviews(session: AsyncSession, limit: int = 50) -> list[dict]:
    from raundo.domain import REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW

    rows = await session.execute(
        select(InvestorReview, InvestorProfile)
        .join(InvestorProfile, InvestorProfile.id == InvestorReview.investor_id)
        .where(InvestorReview.state.in_([REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW]))
        .order_by(InvestorReview.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "review_id": str(review.id),
            "kind": "investor",
            "title": profile.display_name or "Инвестор",
            "state": review.state,
            "revision": review.revision,
            "submitted_at": review.created_at.isoformat(),
        }
        for review, profile in rows.all()
    ]


def suspend_code() -> str:
    return REVIEW_SUSPENDED
