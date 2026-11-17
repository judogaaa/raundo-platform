"""Regression: the publication-consent button must resume the pending submission."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.db.base import Base
from raundo.routers.common import grant_publication_consent
from raundo.services.accounts import AccountService
from raundo.services.consents import CONSENT_PROFILE_PUBLICATION, ConsentService
from raundo.services.startups import StartupService


class _Message:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def answer(self, text: str, **kwargs) -> None:  # noqa: ANN003
        self.texts.append(text)


class _Callback:
    def __init__(self) -> None:
        self.message = _Message()
        self.answers: list[str | None] = []

    async def answer(self, text: str | None = None, **kwargs) -> None:  # noqa: ANN003
        self.answers.append(text)


class _State:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.cleared = False

    async def get_data(self) -> dict:
        return dict(self._data)

    async def clear(self) -> None:
        self.cleared = True


@pytest.mark.asyncio
async def test_publication_consent_grant_resumes_the_pending_startup(monkeypatch) -> None:  # noqa: ANN001
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            user = await AccountService(session).bind_telegram_user(
                telegram_id=779_001,
                username="consent",
                first_name="Consent",
                last_name=None,
                language_code="ru",
            )
            consents = ConsentService(session)
            await consents.accept_required(user.id, source="test")
            startup = await StartupService(session).create_draft(user.id, "Consent Startup")
            await session.commit()

            called: dict[str, str] = {}

            async def _fake_submit(self, target, actor_id) -> None:  # noqa: ANN001
                called["id"] = str(target.id)

            monkeypatch.setattr(StartupService, "submit_for_review", _fake_submit)

            callback = _Callback()
            state = _State(
                {"pending_submit_kind": "startup", "pending_submit_id": str(startup.id)}
            )
            await grant_publication_consent(callback, session, user, state)

            assert called.get("id") == str(startup.id)
            assert await consents.has(user.id, CONSENT_PROFILE_PUBLICATION)
            assert callback.answers
            assert any("модерац" in text.lower() for text in callback.message.texts)
            assert state.cleared
    finally:
        await engine.dispose()
