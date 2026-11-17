"""Reply-keyboard feed UX: exact rows, votes, state, photos, safety."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.billing import UserCollection, UserCollectionItem
from raundo.models.feed import StartupReaction
from raundo.models.startup import Startup, StartupMedia
from raundo.models.user import User
from raundo.routers import feed as feed_router
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_700


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
        self.state = None
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs) -> None:  # noqa: ANN003
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)


class FakeMessage:
    def __init__(self) -> None:
        self.message_id = 100
        self.chat = SimpleNamespace(id=1)
        self.bot = SimpleNamespace(delete_message=AsyncMock())
        self.answer = AsyncMock(return_value=SimpleNamespace(message_id=101))
        self.answer_photo = AsyncMock(return_value=SimpleNamespace(message_id=102))


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
                await session.execute(delete(StartupReaction).where(StartupReaction.startup_id.in_(startups)))
                await session.execute(delete(StartupMedia).where(StartupMedia.startup_id.in_(startups)))
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(delete(UserCollectionItem).where(
                UserCollectionItem.collection_id.in_(
                    select(UserCollection.id).where(UserCollection.user_id.in_(old))
                )
            ))
            await session.execute(delete(UserCollection).where(UserCollection.user_id.in_(old)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _seed(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        viewer = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="feed_ux_viewer",
            first_name="Viewer",
            last_name="One",
            language_code="ru",
        )
        owner = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 2,
            username="feed_ux_owner",
            first_name="Owner",
            last_name="Two",
            language_code="ru",
        )
        await ConsentService(session).accept_all(viewer.id)
        first = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            slug=f"ux-a-{uuid.uuid4().hex[:8]}",
            name="UX Project A",
            one_liner="Первая карточка для проверки ленты",
            full_description="Полное описание первого проекта. " * 5,
            review_state="approved",
            current_revision=1,
            published_at=datetime.now(timezone.utc) + timedelta(days=365),
        )
        second = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            slug=f"ux-b-{uuid.uuid4().hex[:8]}",
            name="UX Project B",
            one_liner="Вторая карточка без фотографии",
            full_description="Полное описание второго проекта.",
            review_state="approved",
            current_revision=1,
            published_at=datetime.now(timezone.utc) + timedelta(days=364),
        )
        hidden = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            slug=f"ux-h-{uuid.uuid4().hex[:8]}",
            name="Hidden Project",
            review_state="submitted",
            current_revision=1,
        )
        session.add_all([first, second, hidden])
        await session.flush()
        session.add(
            StartupMedia(
                id=uuid.uuid4(),
                startup_id=first.id,
                position=1,
                kind="photo",
                telegram_file_id="AgACAgIA-ux",
                file_unique_id="ux-media",
                scan_state="accepted",
            )
        )
        await session.commit()
        return viewer.id, first.id, second.id, hidden.id


def _rows(markup) -> list[list[str]]:  # noqa: ANN001
    return [[button.text for button in row] for row in markup.keyboard]


EXPECTED_ROWS = [
    ["❤️", "👎"],
    ["⭐", "🏆 Топ-10"],
    ["Подробнее"],
    ["🤝 Запрос на знакомство"],
]


@pytest.mark.asyncio
async def test_rate_uses_exact_reply_keyboard_and_state(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, second_id, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        await feed_router.rate_command(message, session, viewer, state)
    if message.answer_photo.await_count:
        markup = message.answer_photo.call_args.kwargs["reply_markup"]
        assert message.answer_photo.call_args.kwargs["caption"]
    else:
        markup = message.answer.call_args.kwargs["reply_markup"]
    assert _rows(markup) == EXPECTED_ROWS
    assert state.data["feed_startup_id"] in {str(first_id), str(second_id)}


@pytest.mark.asyncio
async def test_like_votes_current_and_advances_without_inline_grid(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, second_id, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        await feed_router.rate_command(message, session, viewer, state)
        current = uuid.UUID(state.data["feed_startup_id"])
        await feed_router.on_like(message, session, viewer, state)
        await session.commit()
        reactions = (
            await session.execute(select(StartupReaction))
        ).scalars().all()
        assert len(reactions) == 1
        assert reactions[0].startup_id == current
        assert reactions[0].kind == "like"
        assert uuid.UUID(state.data["feed_startup_id"]) != current
        assert state.data["feed_startup_id"] in {str(first_id), str(second_id)}
    assert not hasattr(message.answer_photo.call_args.kwargs["reply_markup"], "inline_keyboard")


@pytest.mark.asyncio
async def test_rapid_double_like_does_not_rate_next_project(factory) -> None:  # noqa: ANN001
    viewer_id, _, _, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        await feed_router.rate_command(message, session, viewer, state)
        first = uuid.UUID(state.data["feed_startup_id"])
        await feed_router.on_like(message, session, viewer, state)
        await session.commit()
        await feed_router.on_like(message, session, viewer, state)  # immediate second tap
        await session.commit()
        reactions = (await session.execute(select(StartupReaction))).scalars().all()
        assert len(reactions) == 1
        assert reactions[0].startup_id == first


@pytest.mark.asyncio
async def test_dislike_records_dislike_and_advances(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, second_id, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        first = await session.get(Startup, first_id)
        await feed_router._send_card(message, session, viewer, state, first)
        await feed_router.on_dislike(message, session, viewer, state)
        await session.commit()
        reactions = (await session.execute(select(StartupReaction))).scalars().all()
        assert len(reactions) == 1
        assert reactions[0].startup_id == first_id
        assert reactions[0].kind == "dislike"
        assert state.data["feed_startup_id"] == str(second_id)

    # Exactly six buttons, no "Пропустить".
    markup = message.answer.call_args.kwargs["reply_markup"] if message.answer.await_count else message.answer_photo.call_args.kwargs["reply_markup"]
    labels = [button.text for row in markup.keyboard for button in row]
    assert len(labels) == 6
    assert "Пропустить" not in labels


@pytest.mark.asyncio
async def test_favorite_and_details(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, _, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        first = await session.get(Startup, first_id)
        await feed_router._send_card(message, session, viewer, state, first)
        await feed_router.on_favorite(message, session, viewer, state)
        await session.commit()
        favorites = (await session.execute(select(UserCollectionItem))).scalars().all()
        assert len(favorites) == 1
        await feed_router.on_details(message, session, viewer, state)
    text = message.answer.call_args.args[0]
    assert "Полное описание" in text
    assert "startup_id" not in text
    assert "submitted" not in text


@pytest.mark.asyncio
async def test_hidden_startups_never_enter_feed(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, second_id, hidden_id = await _seed(factory)
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        from raundo.services.feed import next_feed_startup

        seen = set()
        startup, _ = await next_feed_startup(session, user_id=viewer.id)
        while startup is not None:
            seen.add(str(startup.id))
            await feed_router.feed_service.react(
                session, user_id=viewer.id, startup_id=startup.id, kind="skip"
            )
            await session.commit()
            startup, _ = await next_feed_startup(session, user_id=viewer.id)
    assert str(hidden_id) not in seen
    assert str(first_id) in seen and str(second_id) in seen


@pytest.mark.asyncio
async def test_photo_and_text_cards_match_current_startup(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, second_id, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        first = await session.get(Startup, first_id)
        second = await session.get(Startup, second_id)
        await feed_router._send_card(message, session, viewer, state, first)
        photo_caption = message.answer_photo.call_args.kwargs["caption"]
        assert "UX Project A" in photo_caption
        assert state.data["feed_startup_id"] == str(first_id)
        await feed_router._send_card(message, session, viewer, state, second)
        text_args = message.answer.call_args
        # No photo for the second project: text card, same reply keyboard.
        assert "UX Project B" in text_args.args[0]
        assert _rows(text_args.kwargs["reply_markup"]) == EXPECTED_ROWS
        assert state.data["feed_startup_id"] == str(second_id)
        message.bot.delete_message.assert_awaited()


@pytest.mark.asyncio
async def test_stale_state_does_not_vote_wrong_project(factory) -> None:  # noqa: ANN001
    viewer_id, first_id, _, _ = await _seed(factory)
    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        viewer = await session.get(User, viewer_id)
        first = await session.get(Startup, first_id)
        await feed_router._send_card(message, session, viewer, state, first)
        shown = state.data["feed_startup_id"]
        # Simulate the startup being archived while the card is open.
        startup = await session.get(Startup, uuid.UUID(shown))
        startup.deleted_at = datetime.now(timezone.utc)
        startup.review_state = "archived"
        await session.commit()
        await feed_router.on_like(message, session, viewer, state)
        await session.commit()
        reactions = (await session.execute(select(StartupReaction))).scalars().all()
        assert all(r.startup_id != startup.id for r in reactions)
