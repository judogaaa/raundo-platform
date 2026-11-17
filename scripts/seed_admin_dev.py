"""Populate raundo_admin_dev with synthetic moderation and support fixtures."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.startup import Startup
from raundo.services.accounts import AccountService
from raundo.services.startups import StartupService


async def _fill_minimal(startup, svc: StartupService) -> None:
    startup.one_liner = "x" * 50
    startup.full_description = "y" * 100
    await svc.set_primary_sector(startup, "ai_ml")
    await svc.set_stage(startup, "mvp")
    startup.website_url = "https://example.com"
    startup.intents = ["feedback"]
    startup.audience_codes = ["b2c"]
    startup.progress_codes = ["mvp"]
    startup.monetization_status = "not_yet"
    startup.fundraising_stage = "not_raising"
    startup.country = "Россия"
    startup.founder_display_name = "Founder"
    startup.founder_role = "CEO"
    await svc.add_photo(
        startup,
        telegram_file_id="file-1",
        file_unique_id="uniq-seed-1",
    )


async def main() -> None:
    factory = get_session_factory()
    async with factory() as session:
        existing = await session.scalar(select(Startup).where(Startup.name == "DevStartup Alpha"))
        if existing is not None:
            print("seed_skip")
            return
        await ensure_catalog_seeded(session)
        owner = await AccountService(session).bind_telegram_user(
            telegram_id=990001,
            username="dev_founder",
            first_name="Dev",
            last_name="Founder",
            language_code="ru",
        )
        svc = StartupService(session)
        startup = await svc.create_draft(owner.id, "DevStartup Alpha")
        await _fill_minimal(startup, svc)
        await svc.submit_for_review(startup, owner.id)
        existing_ticket = await session.scalar(
            select(SupportTicket).where(SupportTicket.public_number == "SUP-000101")
        )
        if existing_ticket is not None:
            await session.commit()
            print(f"seed_ok startup={startup.id}")
            return
        ticket = SupportTicket(
            id=uuid.uuid4(),
            public_number="SUP-000101",
            user_id=owner.id,
            subject="Вопрос по публикации",
            status="new",
            priority="normal",
            updated_at=datetime.now(timezone.utc),
        )
        session.add(ticket)
        await session.flush()
        session.add(
            SupportMessage(
                id=uuid.uuid4(),
                ticket_id=ticket.id,
                author_kind="user",
                body="Подскажите, сколько длится проверка анкеты?",
            )
        )
        await session.commit()
        print(f"seed_ok startup={startup.id} review_state={startup.review_state}")


if __name__ == "__main__":
    asyncio.run(main())
