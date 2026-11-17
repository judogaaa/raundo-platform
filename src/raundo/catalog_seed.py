"""Canonical sector/stage seed shared by migration and tests."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.catalog import Sector, Stage

SECTORS: tuple[tuple[str, str, str], ...] = (
    ("ai_ml", "AI/ML", "AI/ML"),
    ("saas_b2b", "SaaS/B2B", "SaaS/B2B"),
    ("fintech", "FinTech", "FinTech"),
    ("healthtech", "HealthTech", "HealthTech"),
    ("edtech", "EdTech", "EdTech"),
    ("ecommerce", "E-commerce", "E-commerce"),
    ("marketplace", "Marketplace", "Marketplace"),
    ("industry_robotics", "Industry/Robotics", "Industry/Robotics"),
    ("mobility", "Mobility", "Mobility"),
    ("climate_energy", "Climate/Energy", "Climate/Energy"),
    ("consumer", "Consumer", "Consumer"),
    ("gaming", "Gaming", "Gaming"),
    ("proptech", "PropTech", "PropTech"),
    ("security", "Security", "Security"),
    ("media_creator", "Media/Creator", "Media/Creator"),
    ("other", "Другое", "Other"),
)

STAGES: tuple[tuple[str, str, int], ...] = (
    ("idea", "Idea", 1),
    ("prototype", "Prototype", 2),
    ("mvp", "MVP", 3),
    ("launched", "Launched", 4),
    ("revenue", "Revenue", 5),
    ("scaling", "Scaling", 6),
)


async def ensure_catalog_seeded(session: AsyncSession) -> None:
    existing = await session.execute(select(Sector.id).limit(1))
    if existing.scalar_one_or_none() is not None:
        return
    for code, name_ru, name_en in SECTORS:
        session.add(
            Sector(
                id=uuid.uuid4(),
                code=code,
                name_ru=name_ru,
                name_en=name_en,
                active=True,
                taxonomy_version="2026-10",
            )
        )
    for code, label, order in STAGES:
        session.add(
            Stage(id=uuid.uuid4(), code=code, label=label, display_order=order, active=True)
        )
    await session.flush()
