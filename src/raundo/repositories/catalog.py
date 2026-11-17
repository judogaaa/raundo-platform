"""Sector and stage lookups."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.catalog import Sector, Stage


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_sectors(self) -> list[Sector]:
        result = await self._session.execute(
            select(Sector).where(Sector.active.is_(True)).order_by(Sector.name_ru)
        )
        return list(result.scalars().all())

    async def list_stages(self) -> list[Stage]:
        result = await self._session.execute(
            select(Stage).where(Stage.active.is_(True)).order_by(Stage.display_order)
        )
        return list(result.scalars().all())

    async def sector_by_code(self, code: str) -> Sector | None:
        result = await self._session.execute(select(Sector).where(Sector.code == code))
        return result.scalar_one_or_none()

    async def stage_by_code(self, code: str) -> Stage | None:
        result = await self._session.execute(select(Stage).where(Stage.code == code))
        return result.scalar_one_or_none()

    async def sectors_by_codes(self, codes: Collection[str]) -> list[Sector]:
        if not codes:
            return []
        result = await self._session.execute(select(Sector).where(Sector.code.in_(list(codes))))
        return list(result.scalars().all())

    async def stages_by_codes(self, codes: Collection[str]) -> list[Stage]:
        if not codes:
            return []
        result = await self._session.execute(select(Stage).where(Stage.code.in_(list(codes))))
        return list(result.scalars().all())
