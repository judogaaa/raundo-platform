"""Privacy-safe analytics writes."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.system import AnalyticsEvent


def pseudonymize_user_id(user_id: uuid.UUID) -> str:
    digest = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
    return digest[:32]


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def track(
        self,
        event_name: str,
        *,
        user_id: uuid.UUID | None = None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        event = AnalyticsEvent(
            id=uuid.uuid4(),
            user_key_pseudonym=pseudonymize_user_id(user_id) if user_id else None,
            event_name=event_name,
            properties_safe=properties or {},
        )
        self._session.add(event)
        await self._session.flush()
