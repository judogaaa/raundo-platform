"""Telegram identity binding persists internal UUID users."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from raundo.models.user import User, UserConsent
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService


@pytest.mark.asyncio
async def test_upsert_binds_telegram_id_and_updates_username(session) -> None:
    accounts = AccountService(session)
    user = await accounts.bind_telegram_user(
        telegram_id=1001,
        username="old_name",
        first_name="Egor",
        last_name=None,
        language_code="ru",
    )
    assert user.telegram_id == 1001
    assert user.profile is not None
    await session.commit()

    again = await accounts.bind_telegram_user(
        telegram_id=1001,
        username="new_name",
        first_name="Egor",
        last_name="T",
        language_code="ru",
    )
    assert again.id == user.id
    assert again.username == "new_name"
    assert again.last_name == "T"


@pytest.mark.asyncio
async def test_consent_gate_requires_terms_and_privacy(session) -> None:
    accounts = AccountService(session)
    user = await accounts.bind_telegram_user(
        telegram_id=42,
        username=None,
        first_name="A",
        last_name=None,
        language_code="en",
    )
    consents = ConsentService(session)
    assert not await consents.has_required_consents(user.id)
    await consents.accept_all(user.id)
    assert await consents.has_required_consents(user.id)

    rows = (await session.execute(select(UserConsent))).scalars().all()
    kinds = {row.kind for row in rows}
    assert kinds == {"terms", "privacy", "data_processing"}
    assert all(row.policy_version == consents.policy_version for row in rows)


@pytest.mark.asyncio
async def test_users_table_uses_stable_uuid_not_username_pk(session) -> None:
    accounts = AccountService(session)
    user = await accounts.bind_telegram_user(
        telegram_id=7,
        username=None,
        first_name="X",
        last_name=None,
        language_code=None,
    )
    loaded = await session.get(User, user.id)
    assert loaded is not None
    assert loaded.telegram_id == 7
