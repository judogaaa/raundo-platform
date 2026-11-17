"""Outbox enqueue foundation. Delivery worker is a later workstream."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.system import OutboxEvent


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        kind: str,
        dedupe_key: str,
        recipient_user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        safe_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Insert outbox row. Returns False if dedupe_key already exists (idempotent)."""
        event = await self.enqueue_event(
            kind=kind,
            dedupe_key=dedupe_key,
            recipient_user_id=recipient_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            safe_payload=safe_payload,
        )
        return event is not None

    async def enqueue_event(
        self,
        *,
        kind: str,
        dedupe_key: str,
        recipient_user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        safe_payload: dict[str, Any] | None = None,
        event_id: uuid.UUID | None = None,
    ) -> OutboxEvent | None:
        """Insert an outbox row and return it, or None when the dedupe key exists."""
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind is not None else "postgresql"
        payload = safe_payload or {}
        event = OutboxEvent(
            id=event_id or uuid.uuid4(),
            kind=kind,
            recipient_user_id=recipient_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            dedupe_key=dedupe_key,
            safe_payload=payload,
            state="pending",
        )

        if dialect == "postgresql":
            stmt = (
                pg_insert(OutboxEvent)
                .values(
                    id=event.id,
                    kind=kind,
                    recipient_user_id=recipient_user_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    dedupe_key=dedupe_key,
                    safe_payload=payload,
                    state="pending",
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
            result = await self._session.execute(stmt)
            await self._session.flush()
            return event if result.rowcount else None

        found = await self._session.execute(
            select(OutboxEvent).where(OutboxEvent.dedupe_key == dedupe_key)
        )
        if found.scalar_one_or_none() is not None:
            return None
        self._session.add(event)
        await self._session.flush()
        return event
