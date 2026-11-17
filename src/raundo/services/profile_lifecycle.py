"""Re-moderation, revision supersession, and soft delete for profiles."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import (
    REVIEW_APPROVED,
    REVIEW_ARCHIVED,
    REVIEW_PENDING_CHANGES,
    REVIEW_SUBMITTED,
    REVIEW_SUPERSEDED,
    REVIEW_UNDER_REVIEW,
)
from raundo.models.investor import InvestorProfile, InvestorReview
from raundo.models.startup import Startup, StartupMedia, StartupReview
from raundo.published_snapshot import (
    investor_snapshot_payload,
    startup_snapshot_payload,
    strip_sensitive_from_snapshot,
)

_IN_REVIEW_STATES = frozenset({REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW})
_OPEN_REVIEW_STATES = frozenset({REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW})


async def _supersede_startup_reviews(session: AsyncSession, startup_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(StartupReview)
        .where(
            StartupReview.startup_id == startup_id,
            StartupReview.state.in_(_OPEN_REVIEW_STATES),
        )
        .values(state=REVIEW_SUPERSEDED, decided_at=now)
    )


async def _supersede_investor_reviews(session: AsyncSession, investor_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(InvestorReview)
        .where(
            InvestorReview.investor_id == investor_id,
            InvestorReview.state.in_(_OPEN_REVIEW_STATES),
        )
        .values(state=REVIEW_SUPERSEDED, decided_at=now)
    )


async def _startup_media(session: AsyncSession, startup_id: uuid.UUID) -> list[StartupMedia]:
    result = await session.execute(
        select(StartupMedia).where(StartupMedia.startup_id == startup_id).order_by(StartupMedia.position)
    )
    return list(result.scalars().all())


async def begin_startup_approved_edit(session: AsyncSession, startup: Startup) -> None:
    """Freeze the last approved public view before the owner mutates fields."""
    if startup.review_state != REVIEW_APPROVED:
        return
    if startup.published_snapshot is None:
        media = await _startup_media(session, startup.id)
        startup.published_snapshot = startup_snapshot_payload(startup, media)
    startup.review_state = REVIEW_PENDING_CHANGES


async def begin_investor_approved_edit(session: AsyncSession, profile: InvestorProfile) -> None:
    if profile.review_status != REVIEW_APPROVED:
        return
    if profile.published_snapshot is None:
        sector_ids, stage_ids = await _investor_pref_ids(session, profile.id)
        profile.published_snapshot = investor_snapshot_payload(
            profile, sector_ids=sector_ids, stage_ids=stage_ids
        )
    profile.review_status = REVIEW_PENDING_CHANGES


async def register_startup_owner_change(session: AsyncSession, startup: Startup) -> None:
    """After an owner edits a profile that left draft-only moderation."""
    state = startup.review_state
    if state in _IN_REVIEW_STATES:
        startup.current_revision += 1
        await _supersede_startup_reviews(session, startup.id)
        session.add(
            StartupReview(
                id=uuid.uuid4(),
                startup_id=startup.id,
                revision=startup.current_revision,
                state=REVIEW_SUBMITTED,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
        return
    if state == REVIEW_PENDING_CHANGES:
        return


async def register_investor_owner_change(session: AsyncSession, profile: InvestorProfile) -> None:
    state = profile.review_status
    if state in _IN_REVIEW_STATES:
        profile.current_revision += 1
        await _supersede_investor_reviews(session, profile.id)
        session.add(
            InvestorReview(
                id=uuid.uuid4(),
                investor_id=profile.id,
                revision=profile.current_revision,
                state=REVIEW_SUBMITTED,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
        return
    if state == REVIEW_PENDING_CHANGES:
        return


async def _investor_pref_ids(session: AsyncSession, investor_id: uuid.UUID) -> tuple[list[str], list[str]]:
    from raundo.models.investor import InvestorSectorPreference, InvestorStagePreference

    sector_rows = await session.execute(
        select(InvestorSectorPreference.sector_id).where(
            InvestorSectorPreference.investor_id == investor_id
        )
    )
    stage_rows = await session.execute(
        select(InvestorStagePreference.stage_id).where(
            InvestorStagePreference.investor_id == investor_id
        )
    )
    return (
        [str(x) for x in sector_rows.scalars().all()],
        [str(x) for x in stage_rows.scalars().all()],
    )


async def withdraw_startup_sensitive_public(
    session: AsyncSession, startup: Startup
) -> None:
    if startup.published_snapshot:
        startup.published_snapshot = strip_sensitive_from_snapshot(startup.published_snapshot)
    if startup.review_state == REVIEW_APPROVED:
        await begin_startup_approved_edit(session, startup)


async def withdraw_investor_sensitive_public(
    session: AsyncSession, profile: InvestorProfile
) -> None:
    if profile.published_snapshot:
        profile.published_snapshot = strip_sensitive_from_snapshot(profile.published_snapshot)
    if profile.review_status == REVIEW_APPROVED:
        await begin_investor_approved_edit(session, profile)


async def soft_delete_startup(
    session: AsyncSession, startup: Startup, owner_user_id: uuid.UUID
) -> None:
    if startup.owner_user_id != owner_user_id:
        raise ValueError("owner_mismatch")
    if startup.deleted_at is not None:
        return
    now = datetime.now(timezone.utc)
    startup.deleted_at = now
    startup.review_state = REVIEW_ARCHIVED
    startup.published_snapshot = None
    startup.published_at = None
    await _supersede_startup_reviews(session, startup.id)
    await session.flush()


async def soft_delete_investor(
    session: AsyncSession, profile: InvestorProfile, user_id: uuid.UUID
) -> None:
    if profile.user_id != user_id:
        raise ValueError("owner_mismatch")
    if profile.deleted_at is not None:
        return
    now = datetime.now(timezone.utc)
    profile.deleted_at = now
    profile.review_status = REVIEW_ARCHIVED
    profile.published_snapshot = None
    profile.approved_at = None
    await _supersede_investor_reviews(session, profile.id)
    await session.flush()


def startup_has_public_snapshot(startup: Startup) -> bool:
    return bool(
        startup.deleted_at is None
        and startup.published_snapshot
        and startup.review_state == REVIEW_PENDING_CHANGES
    )


def investor_has_public_snapshot(profile: InvestorProfile) -> bool:
    return bool(
        profile.deleted_at is None
        and profile.published_snapshot
        and profile.review_status == REVIEW_PENDING_CHANGES
    )
