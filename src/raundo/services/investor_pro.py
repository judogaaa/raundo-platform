"""Investor PRO: saved searches, radar prefs, pipeline, private notes.

Nothing here discloses private contacts, admin proofs or non-public form
fields. Notes are readable only by their author; pipeline entries belong to
one investor. Feed-dependent features (radar, comparison) store intent and
wait for the discovery feed (Issue #3) — honestly marked, never faked.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import (
    InvestorPipelineEntry,
    InvestorPrivateNote,
    InvestorSavedSearch,
)
from raundo.models.startup import Startup
from raundo.premium_catalog import PLAN_INVESTOR_PRO
from raundo.services.premium import PremiumService

PIPELINE_INTERESTED = "interested"
PIPELINE_STUDYING = "studying"
PIPELINE_REQUESTED = "requested"
PIPELINE_TALKING = "talking"
PIPELINE_NOT_FIT = "not_fit"

PIPELINE_STAGES = (
    PIPELINE_INTERESTED,
    PIPELINE_STUDYING,
    PIPELINE_REQUESTED,
    PIPELINE_TALKING,
    PIPELINE_NOT_FIT,
)

PIPELINE_STAGE_LABELS: dict[str, str] = {
    PIPELINE_INTERESTED: "Интересно",
    PIPELINE_STUDYING: "Изучаю",
    PIPELINE_REQUESTED: "Запрошен контакт",
    PIPELINE_TALKING: "Общаемся",
    PIPELINE_NOT_FIT: "Не подходит",
}

MAX_SAVED_SEARCHES = 20
MAX_PIPELINE_ENTRIES = 100


class InvestorProError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class InvestorProService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _require_entitlement(self, user_id: uuid.UUID) -> None:
        premium = PremiumService(self._session)
        if not await premium.has_active_user_plan(user_id, PLAN_INVESTOR_PRO):
            raise InvestorProError("Требуется активный Investor PRO.", code="premium_required")

    # ------------------------------------------------------------ saved searches

    async def saved_searches(self, user_id: uuid.UUID) -> list[InvestorSavedSearch]:
        result = await self._session.scalars(
            select(InvestorSavedSearch)
            .where(InvestorSavedSearch.user_id == user_id)
            .order_by(InvestorSavedSearch.created_at)
        )
        return list(result.all())

    async def create_saved_search(
        self,
        user_id: uuid.UUID,
        name: str,
        filters: dict,
        *,
        is_default: bool = False,
    ) -> InvestorSavedSearch:
        await self._require_entitlement(user_id)
        clean = name.strip()
        if not (1 <= len(clean) <= 80):
            raise InvestorProError("Название стратегии: от 1 до 80 символов.", code="bad_name")
        if not isinstance(filters, dict):
            raise InvestorProError("Фильтры должны быть словарём.", code="bad_filters")
        existing = await self.saved_searches(user_id)
        if len(existing) >= MAX_SAVED_SEARCHES:
            raise InvestorProError("Достигнут лимит сохранённых стратегий.", code="limit")
        if is_default:
            for search in existing:
                search.is_default = False
        saved = InvestorSavedSearch(
            id=uuid.uuid4(),
            user_id=user_id,
            name=clean,
            filters=filters,
            is_default=is_default,
        )
        self._session.add(saved)
        await self._session.flush()
        return saved

    async def delete_saved_search(self, user_id: uuid.UUID, search_id: uuid.UUID) -> bool:
        """Only the author's own row can be removed — never another investor's."""
        saved = await self._session.get(InvestorSavedSearch, search_id)
        if saved is None or saved.user_id != user_id:
            return False
        await self._session.delete(saved)
        await self._session.flush()
        return True

    # ---------------------------------------------------------------- pipeline

    async def pipeline_entries(self, user_id: uuid.UUID) -> list[InvestorPipelineEntry]:
        result = await self._session.scalars(
            select(InvestorPipelineEntry)
            .where(InvestorPipelineEntry.user_id == user_id)
            .order_by(InvestorPipelineEntry.created_at)
        )
        return list(result.all())

    async def set_pipeline_stage(
        self,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        stage: str,
        *,
        note: str | None = None,
    ) -> InvestorPipelineEntry:
        await self._require_entitlement(user_id)
        if stage not in PIPELINE_STAGES:
            raise InvestorProError("Неизвестная стадия воронки.", code="bad_stage")
        startup = await self._session.get(Startup, startup_id)
        if startup is None:
            raise InvestorProError("Стартап не найден.", code="not_found")
        existing = (
            await self._session.scalars(
                select(InvestorPipelineEntry).where(
                    InvestorPipelineEntry.user_id == user_id,
                    InvestorPipelineEntry.startup_id == startup_id,
                )
            )
        ).first()
        if existing is not None:
            existing.stage = stage
            if note is not None:
                existing.note = note[:240]
            await self._session.flush()
            return existing
        if len(await self.pipeline_entries(user_id)) >= MAX_PIPELINE_ENTRIES:
            raise InvestorProError("Достигнут лимит воронки.", code="limit")
        entry = InvestorPipelineEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            startup_id=startup_id,
            stage=stage,
            note=note[:240] if note is not None else None,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    # ------------------------------------------------------------ private notes

    async def private_notes(self, user_id: uuid.UUID) -> list[InvestorPrivateNote]:
        """Strictly author-scoped: a callback can never read someone else's."""
        result = await self._session.scalars(
            select(InvestorPrivateNote)
            .where(InvestorPrivateNote.user_id == user_id)
            .order_by(InvestorPrivateNote.created_at.desc())
        )
        return list(result.all())

    async def add_private_note(
        self,
        user_id: uuid.UUID,
        body: str,
        *,
        startup_id: uuid.UUID | None = None,
    ) -> InvestorPrivateNote:
        await self._require_entitlement(user_id)
        clean = body.strip()
        if not clean:
            raise InvestorProError("Пустая заметка.", code="bad_note")
        if startup_id is not None:
            startup = await self._session.get(Startup, startup_id)
            if startup is None:
                raise InvestorProError("Стартап не найден.", code="not_found")
        note = InvestorPrivateNote(
            id=uuid.uuid4(),
            user_id=user_id,
            startup_id=startup_id,
            body=clean,
        )
        self._session.add(note)
        await self._session.flush()
        return note

    async def get_private_note(
        self, user_id: uuid.UUID, note_id: uuid.UUID
    ) -> Optional[InvestorPrivateNote]:
        """Cross-user reads return None — the security boundary is the query."""
        return (
            await self._session.scalars(
                select(InvestorPrivateNote).where(
                    InvestorPrivateNote.id == note_id,
                    InvestorPrivateNote.user_id == user_id,
                )
            )
        ).first()
