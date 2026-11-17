"""Startup feed, reactions and 14-day top rankings (transparent count-based scorer)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import get_settings
from raundo.domain import REVIEW_APPROVED
from raundo.models.billing import UserCollection, UserCollectionItem
from raundo.models.feed import (
    REACTION_DISLIKE,
    REACTION_KINDS,
    REACTION_LIKE,
    REACTION_SKIP,
    StartupReaction,
)
from raundo.models.investor import InvestorProfile
from raundo.models.organization import OrganizationMembership
from raundo.models.startup import Startup, StartupMedia
from raundo.models.user import User
from raundo.services.community_plus import CommunityPlusService

DEFAULT_EPOCH = "2026-10-01T00:00:00+00:00"
PERIOD_DAYS = 14


class FeedError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Period:
    index: int
    start: datetime
    end: datetime


def _epoch() -> datetime:
    raw = getattr(get_settings(), "ranking_epoch_utc", DEFAULT_EPOCH) or DEFAULT_EPOCH
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def current_period(now: datetime | None = None) -> Period:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = moment - _epoch()
    index = max(0, delta.days // PERIOD_DAYS)
    start = _epoch() + timedelta(days=PERIOD_DAYS * index)
    return Period(index=index, start=start, end=start + timedelta(days=PERIOD_DAYS))


async def _published_startups(session: AsyncSession) -> list[Startup]:
    rows = await session.execute(
        select(Startup).where(
            Startup.review_state == REVIEW_APPROVED,
            Startup.deleted_at.is_(None),
            Startup.published_at.is_not(None),
        )
    )
    return list(rows.scalars().all())


async def next_feed_startup(
    session: AsyncSession, *, user_id: uuid.UUID, startup_id: uuid.UUID | None = None
) -> tuple[Startup | None, StartupReaction | None]:
    """Next card: explicit id (if visible) or the newest startup without a vote this period."""
    period = current_period()
    if startup_id is not None:
        startup = await session.get(Startup, startup_id)
        if (
            startup is None
            or startup.deleted_at is not None
            or startup.review_state != REVIEW_APPROVED
            or startup.published_at is None
        ):
            raise FeedError("Проект больше недоступен.", code="hidden")
        reaction = await session.scalar(
            select(StartupReaction).where(
                StartupReaction.startup_id == startup.id,
                StartupReaction.user_id == user_id,
                StartupReaction.period_index == period.index,
            )
        )
        return startup, reaction

    reacted = select(StartupReaction.startup_id).where(
        StartupReaction.user_id == user_id,
        StartupReaction.period_index == period.index,
    )
    candidates = [
        s
        for s in await _published_startups(session)
        if s.owner_user_id != user_id and s.id not in set((await session.execute(reacted)).scalars())
    ]
    candidates.sort(key=lambda s: s.published_at or s.created_at, reverse=True)
    return (candidates[0], None) if candidates else (None, None)


async def reaction_counts(
    session: AsyncSession, *, startup_id: uuid.UUID, period_index: int
) -> dict[str, int]:
    rows = await session.execute(
        select(StartupReaction.kind, func.count())
        .where(
            StartupReaction.startup_id == startup_id,
            StartupReaction.period_index == period_index,
        )
        .group_by(StartupReaction.kind)
    )
    return {kind: int(count) for kind, count in rows.all()}


async def react(
    session: AsyncSession, *, user_id: uuid.UUID, startup_id: uuid.UUID, kind: str
) -> dict[str, int]:
    if kind not in REACTION_KINDS:
        raise FeedError("Неизвестная оценка.", code="bad_kind")
    startup = await session.get(Startup, startup_id)
    if (
        startup is None
        or startup.deleted_at is not None
        or startup.review_state != REVIEW_APPROVED
        or startup.published_at is None
    ):
        raise FeedError("Проект больше недоступен.", code="hidden")
    if startup.owner_user_id == user_id:
        raise FeedError("Нельзя оценивать собственный проект.", code="self_vote")

    period = current_period()
    existing = await session.scalar(
        select(StartupReaction).where(
            StartupReaction.startup_id == startup_id,
            StartupReaction.user_id == user_id,
            StartupReaction.period_index == period.index,
        )
    )
    if existing is None:
        existing = StartupReaction(
            id=uuid.uuid4(),
            startup_id=startup_id,
            user_id=user_id,
            kind=kind,
            period_index=period.index,
        )
        session.add(existing)
    else:
        existing.kind = kind
    await session.flush()
    return await reaction_counts(session, startup_id=startup_id, period_index=period.index)


async def toggle_favorite(
    session: AsyncSession, *, user_id: uuid.UUID, startup_id: uuid.UUID
) -> bool:
    startup = await session.get(Startup, startup_id)
    if startup is None or startup.deleted_at is not None or startup.review_state != REVIEW_APPROVED:
        raise FeedError("Проект больше недоступен.", code="hidden")
    service = CommunityPlusService(session)
    collection = await service.ensure_default_collection(user_id)
    existing = await session.scalar(
        select(func.count())
        .select_from(UserCollectionItem)
        .where(
            UserCollectionItem.collection_id == collection.id,
            UserCollectionItem.startup_id == startup_id,
        )
    )
    if existing:
        await session.execute(
            UserCollectionItem.__table__.delete().where(
                UserCollectionItem.collection_id == collection.id,
                UserCollectionItem.startup_id == startup_id,
            )
        )
        await session.flush()
        return False
    await service.add_item(collection, startup_id)
    return True


async def is_favorite(
    session: AsyncSession, *, user_id: uuid.UUID, startup_id: uuid.UUID
) -> bool:
    row = await session.scalar(
        select(func.count())
        .select_from(UserCollectionItem)
        .join(UserCollection, UserCollection.id == UserCollectionItem.collection_id)
        .where(
            UserCollection.user_id == user_id,
            UserCollectionItem.startup_id == startup_id,
        )
    )
    return bool(row)


async def _verified_voter_ids(session: AsyncSession) -> set[uuid.UUID]:
    """Approved human direct investors or representatives with an approved mandate."""
    ids: set[uuid.UUID] = set()
    rows = await session.execute(
        select(InvestorProfile.user_id).where(
            InvestorProfile.review_status == REVIEW_APPROVED,
            InvestorProfile.deleted_at.is_(None),
            InvestorProfile.claims_direct_investment.is_(True),
        )
    )
    ids.update(rows.scalars().all())
    mandate_rows = await session.execute(
        select(OrganizationMembership.user_id).where(
            OrganizationMembership.status == "approved",
            OrganizationMembership.claims_direct_investment_mandate.is_(True),
        )
    )
    approved_investors = set(
        (
            await session.execute(
                select(InvestorProfile.user_id).where(
                    InvestorProfile.review_status == REVIEW_APPROVED,
                    InvestorProfile.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    ids.update(uid for uid in mandate_rows.scalars().all() if uid in approved_investors)
    return ids


async def top_startups(
    session: AsyncSession,
    *,
    cohort: str = "community",
    limit: int = 10,
) -> tuple[Period, list[dict]]:
    """Count-based ranking: qualified unique likes in the current period.

    Excludes self-votes, restricted users and (for the investor cohort) anyone
    without an approved direct-investment mandate. Premium never participates.
    """
    if cohort not in {"community", "verified_investors"}:
        raise FeedError("Неизвестный рейтинг.", code="bad_cohort")
    period = current_period()
    qualified = {REACTION_LIKE}
    if cohort == "verified_investors":
        voters = await _verified_voter_ids(session)
        if not voters:
            return period, []
    else:
        voters = None

    stmt = (
        select(
            StartupReaction.startup_id,
            func.count(func.distinct(StartupReaction.user_id)).label("likes"),
        )
        .join(Startup, Startup.id == StartupReaction.startup_id)
        .join(User, User.id == StartupReaction.user_id)
        .where(
            StartupReaction.period_index == period.index,
            StartupReaction.kind.in_(qualified),
            Startup.deleted_at.is_(None),
            Startup.review_state == REVIEW_APPROVED,
            Startup.published_at.is_not(None),
            User.status == "active",
            Startup.owner_user_id != StartupReaction.user_id,
        )
        .group_by(StartupReaction.startup_id)
        .order_by(func.count(func.distinct(StartupReaction.user_id)).desc())
        .limit(limit)
    )
    if voters is not None:
        stmt = stmt.where(StartupReaction.user_id.in_(voters))
    rows = (await session.execute(stmt)).all()

    items: list[dict] = []
    for position, (startup_id, likes) in enumerate(rows, start=1):
        startup = await session.get(Startup, startup_id)
        if startup is None:
            continue
        items.append({"position": position, "startup": startup, "likes": int(likes)})
    return period, items


async def first_media_file_id(session: AsyncSession, startup_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(StartupMedia.telegram_file_id)
        .where(StartupMedia.startup_id == startup_id)
        .order_by(StartupMedia.position)
        .limit(1)
    )


def reaction_label(kind: str) -> str:
    return {
        REACTION_LIKE: "❤️ Оценён",
        REACTION_DISLIKE: "👎 Не интересен",
        REACTION_SKIP: "Пропущен",
    }.get(kind, "")
