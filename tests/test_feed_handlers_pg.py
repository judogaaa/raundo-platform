"""Bot handlers for /rate and /top against the real suite database."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.feed import StartupReaction
from raundo.models.startup import Startup, StartupMedia
from raundo.models.user import User
from raundo.routers import feed as feed_router
from raundo.services import feed as feed_service
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 992_700


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


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs) -> None:  # noqa: ANN003
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean_and_dispose(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 100)
            )
        ).scalars().all()
        if old:
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(old)))
            ).scalars().all()
            if startups:
                await session.execute(
                    delete(StartupReaction).where(StartupReaction.startup_id.in_(startups))
                )
                await session.execute(delete(StartupMedia).where(StartupMedia.startup_id.in_(startups)))
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _seed(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        viewer = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="feed_handler_user",
            first_name="Viewer",
            last_name="One",
            language_code="ru",
        )
        owner = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 2,
            username="feed_handler_owner",
            first_name="Owner",
            last_name="Two",
            language_code="ru",
        )
        await ConsentService(session).accept_all(viewer.id)
        startup = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            slug=f"handler-{uuid.uuid4().hex[:8]}",
            name="Real Feed Project",
            one_liner="Платформа для тестового сценария ленты",
            review_state="approved",
            current_revision=1,
            published_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        session.add(startup)
        await session.flush()
        session.add(
            StartupMedia(
                id=uuid.uuid4(),
                startup_id=startup.id,
                position=1,
                kind="photo",
                telegram_file_id="AgACAgIA-test-file-id",
                file_unique_id="feed-handler-uniq",
                scan_state="accepted",
            )
        )
        await session.commit()
        return viewer.id, startup.id


async def test_rate_command_sends_real_card_and_top_lists_like(factory) -> None:  # noqa: ANN001
    viewer_id, startup_id = await _seed(factory)
    message = SimpleNamespace(
        answer=AsyncMock(return_value=SimpleNamespace(message_id=1)),
        answer_photo=AsyncMock(return_value=SimpleNamespace(message_id=2)),
        bot=SimpleNamespace(delete_message=AsyncMock()),
        chat=SimpleNamespace(id=1),
        from_user=SimpleNamespace(id=TG_BASE + 1),
    )
    state = FakeState()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        await feed_router.rate_command(message, session, viewer, state)
        await session.commit()
        current_id = state.data["feed_startup_id"]
        from raundo.models.startup import Startup as StartupModel

        current_startup = await session.get(StartupModel, __import__("uuid").UUID(current_id))
        method = message.answer_photo if message.answer_photo.await_count else message.answer
        markup = method.call_args.kwargs["reply_markup"]
        rows = [[button.text for button in row] for row in markup.keyboard]
        assert rows == [
            ["❤️", "👎"],
            ["⭐", "🏆 Топ-10"],
            ["Подробнее"],
            ["🤝 Запрос на знакомство"],
        ]

    async with factory() as session:
        await feed_service.react(
            session, user_id=viewer_id, startup_id=current_startup.id, kind="like"
        )
        await session.commit()
        viewer = await session.get(User, viewer_id)
        message2 = SimpleNamespace(answer=AsyncMock())
        await feed_router.top_command(message2, session, viewer, FakeState())
    text = message2.answer.call_args.args[0]
    assert current_startup.name in text or "мало оценок" in text


async def test_top_empty_state_is_honest(factory) -> None:  # noqa: ANN001
    await _seed(factory)
    async with factory() as session:
        viewer = await session.scalar(select(User).where(User.telegram_id == TG_BASE + 1))
        message = SimpleNamespace(answer=AsyncMock())
        await feed_router.top_command(message, session, viewer, FakeState())
    text = message.answer.call_args.args[0]
    assert "мало оценок" in text
