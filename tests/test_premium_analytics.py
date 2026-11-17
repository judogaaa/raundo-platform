"""Startup analytics: real observed events only; honest zeros without a feed."""

from __future__ import annotations

import uuid

import pytest

from raundo.services.startup_analytics import (
    EVENT_IMPRESSION,
    EVENT_LINK_CLICK,
    StartupAnalyticsService,
    pseudonymize_viewer,
)


async def _startup(session, telegram_id: int, name: str):
    from raundo.services.accounts import AccountService
    from raundo.services.startups import StartupService

    owner = await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="T",
        last_name=None,
        language_code="ru",
    )
    return await StartupService(session).create_draft(owner.id, name)


@pytest.mark.asyncio
async def test_summary_without_feed_is_honest_zero(session) -> None:
    startup = await _startup(session, 301, "NoData")
    summary = await StartupAnalyticsService(session).summary(startup.id)
    assert summary[EVENT_IMPRESSION] == 0
    assert summary["unique_viewers"] == 0


@pytest.mark.asyncio
async def test_record_and_dedupe(session) -> None:
    startup = await _startup(session, 302, "Events")
    analytics = StartupAnalyticsService(session)
    pseudo = pseudonymize_viewer(uuid.uuid4(), salt=str(startup.id))
    assert await analytics.record(
        startup_id=startup.id,
        event_type=EVENT_IMPRESSION,
        user_key_pseudonym=pseudo,
        source="organic",
        dedupe_key=f"imp:{startup.id}:{pseudo}",
    )
    # Same dedupe key → ignored, not double counted.
    assert not await analytics.record(
        startup_id=startup.id,
        event_type=EVENT_IMPRESSION,
        user_key_pseudonym=pseudo,
        dedupe_key=f"imp:{startup.id}:{pseudo}",
    )
    await analytics.record(
        startup_id=startup.id,
        event_type=EVENT_LINK_CLICK,
        user_key_pseudonym=pseudo,
    )
    summary = await analytics.summary(startup.id)
    assert summary[EVENT_IMPRESSION] == 1
    assert summary[EVENT_LINK_CLICK] == 1
    assert summary["unique_viewers"] == 1


@pytest.mark.asyncio
async def test_unknown_event_type_rejected(session) -> None:
    startup = await _startup(session, 303, "Strict")
    with pytest.raises(ValueError):
        await StartupAnalyticsService(session).record(
            startup_id=startup.id, event_type="made_up_metric"
        )


@pytest.mark.asyncio
async def test_pseudonym_never_contains_raw_id(session) -> None:
    raw = uuid.uuid4()
    pseudo = pseudonymize_viewer(raw, salt="s")
    assert str(raw) not in pseudo
    assert len(pseudo) == 32
    assert pseudo == pseudonymize_viewer(raw, salt="s")
