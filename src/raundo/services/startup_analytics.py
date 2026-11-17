"""Startup analytics: real, observed events only — no invented metrics.

Events are recorded when the product actually observes them (feed views,
detail opens, link clicks, favorites, contact requests). Nothing else writes
fake rows: with no feed yet, summaries honestly return zeros and the Telegram
UI says «Данных пока нет».
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import StartupAnalyticsEvent

EVENT_IMPRESSION = "impression"
EVENT_UNIQUE_VIEW = "unique_view"
EVENT_DETAIL_OPEN = "detail_open"
EVENT_LINK_CLICK = "link_click"
EVENT_FAVORITE_ADD = "favorite_add"
EVENT_CONTACT_REQUEST = "contact_request"

KNOWN_EVENT_TYPES = (
    EVENT_IMPRESSION,
    EVENT_UNIQUE_VIEW,
    EVENT_DETAIL_OPEN,
    EVENT_LINK_CLICK,
    EVENT_FAVORITE_ADD,
    EVENT_CONTACT_REQUEST,
)

SOURCE_ORGANIC = "organic"
SOURCE_BOOSTED = "boosted"


class StartupAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        startup_id: uuid.UUID,
        event_type: str,
        user_key_pseudonym: str | None = None,
        source: str = SOURCE_ORGANIC,
        dedupe_key: str | None = None,
    ) -> bool:
        """
        Store one observed event. Returns False when dedupe_key already seen.

        user_key_pseudonym is a hashed viewer key, never a Telegram id.
        """
        if event_type not in KNOWN_EVENT_TYPES:
            raise ValueError(f"Unknown analytics event type: {event_type}")
        if dedupe_key:
            existing = await self._session.scalar(
                select(StartupAnalyticsEvent.id).where(
                    StartupAnalyticsEvent.dedupe_key == dedupe_key
                )
            )
            if existing is not None:
                return False
        self._session.add(
            StartupAnalyticsEvent(
                id=uuid.uuid4(),
                startup_id=startup_id,
                event_type=event_type,
                user_key_pseudonym=user_key_pseudonym,
                source=source,
                dedupe_key=dedupe_key,
            )
        )
        await self._session.flush()
        return True

    async def unique_viewers(self, startup_id: uuid.UUID, *, since: datetime | None = None) -> int:
        """Transparent methodology: distinct pseudonymous viewer keys observed."""
        stmt = (
            select(func.count(func.distinct(StartupAnalyticsEvent.user_key_pseudonym)))
            .where(
                StartupAnalyticsEvent.startup_id == startup_id,
                StartupAnalyticsEvent.event_type == EVENT_IMPRESSION,
                StartupAnalyticsEvent.user_key_pseudonym.is_not(None),
            )
        )
        if since is not None:
            stmt = stmt.where(StartupAnalyticsEvent.created_at >= since)
        value = await self._session.scalar(stmt)
        return int(value or 0)

    async def summary(
        self, startup_id: uuid.UUID, *, since: datetime | None = None
    ) -> dict[str, int]:
        """Real counts only. Empty storage → honest zeros, never fakes."""
        stmt = (
            select(StartupAnalyticsEvent.event_type, func.count(StartupAnalyticsEvent.id))
            .where(StartupAnalyticsEvent.startup_id == startup_id)
            .group_by(StartupAnalyticsEvent.event_type)
        )
        if since is not None:
            stmt = stmt.where(StartupAnalyticsEvent.created_at >= since)
        counts = {event_type: 0 for event_type in KNOWN_EVENT_TYPES}
        for event_type, count in (await self._session.execute(stmt)).all():
            counts[event_type] = int(count)
        counts["unique_viewers"] = await self.unique_viewers(startup_id, since=since)
        return counts


def pseudonymize_viewer(user_id: uuid.UUID, salt: str) -> str:
    """Deterministic per-startup-salt pseudonym; raw ids are never stored."""
    import hashlib

    return hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()[:32]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
