"""Rendered /profile HTML: valid entities, no raw markup from user data."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.investor import InvestorProfile
from raundo.models.startup import Startup, StartupReview
from raundo.models.user import User
from raundo.routers.profile import _profile_text
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_300


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    if "raundo_local_suite" not in os.environ.get("DATABASE_URL", ""):
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        self.tags.append(tag)

    def handle_data(self, data):  # noqa: ANN001
        self.text.append(data)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 50)
            )
        ).scalars().all()
        if old:
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(old)))
            ).scalars().all()
            if startups:
                await session.execute(delete(StartupReview).where(StartupReview.startup_id.in_(startups)))
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(delete(InvestorProfile).where(InvestorProfile.user_id.in_(old)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def test_profile_is_html_safe_with_hostile_names(factory) -> None:  # noqa: ANN001
    hostile = "<b>Взлом</b> & <script>"
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="profile_user",
            first_name=hostile,
            last_name="<i>",
            language_code="ru",
        )
        session.add(
            Startup(
                id=uuid.uuid4(),
                owner_user_id=user.id,
                slug=f"profile-{uuid.uuid4().hex[:8]}",
                name=hostile,
                review_state="pending_changes",
                current_revision=1,
                published_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        await InvestorService(session).get_or_create_draft(user.id)
        await session.commit()
        text = await _profile_text(session, user)

    parser = _Collector()
    parser.feed(text)
    assert set(parser.tags) <= {"b", "i", "code", "a"}, parser.tags
    joined = "\n".join(parser.text)
    # The user's literal text survives (escaped in the wire format, decoded here).
    assert "<b>Взлом</b>" in joined
    assert "<script>" in joined
    # Our own heading renders as markup, not literal tags.
    assert "Мой профиль" in joined
    assert "None" not in text
    assert "pending_changes" not in text
    assert "Требуются изменения" in text
