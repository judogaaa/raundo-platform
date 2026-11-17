"""Outbox enqueue is idempotent by dedupe_key."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.analytics import AnalyticsService, pseudonymize_user_id
from raundo.services.notifications import NotificationService


@pytest.mark.asyncio
async def test_outbox_dedupe_and_analytics_pseudonym(session) -> None:
    accounts = AccountService(session)
    user = await accounts.bind_telegram_user(
        telegram_id=9,
        username="u",
        first_name="N",
        last_name=None,
        language_code="ru",
    )
    notifications = NotificationService(session)
    first = await notifications.enqueue(
        kind="user.consent_accepted",
        dedupe_key=f"consent:{user.id}:v1",
        recipient_user_id=user.id,
        safe_payload={"policy_version": "v1"},
    )
    second = await notifications.enqueue(
        kind="user.consent_accepted",
        dedupe_key=f"consent:{user.id}:v1",
        recipient_user_id=user.id,
        safe_payload={"policy_version": "v1"},
    )
    assert first is True
    assert second is False
    rows = (await session.execute(select(OutboxEvent))).scalars().all()
    assert len(rows) == 1

    analytics = AnalyticsService(session)
    await analytics.track("bot_started", user_id=user.id, properties={"source": "test"})
    assert pseudonymize_user_id(user.id) != str(user.id)
