"""End-to-end UX of profile, support, complaints and the empty feed (PG)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.billing import UserCollection, UserCollectionItem
from raundo.models.contact import ContactRequest
from raundo.models.feed import StartupReaction
from raundo.models.startup import Startup, StartupMedia
from raundo.models.user import User
from raundo.routers import complaints as complaints_router
from raundo.routers import profile as profile_router
from raundo.routers import support as support_router
from raundo.services import contact_requests as contacts
from raundo.services import feed as feed_service
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_900


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
        self.current: str | None = None
        self.set_state = AsyncMock()
        self.clear = AsyncMock()

    async def update_data(self, **kwargs) -> None:  # noqa: ANN003
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def get_state(self) -> str | None:
        return self.current


class FakeMessage:
    def __init__(self) -> None:
        self.message_id = 10
        self.chat = SimpleNamespace(id=1)
        self.bot = SimpleNamespace(delete_message=AsyncMock())
        self.answer = AsyncMock(return_value=SimpleNamespace(message_id=11))
        self.answer_photo = AsyncMock(return_value=SimpleNamespace(message_id=12))


class FakeCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answer = AsyncMock()
        self.message = FakeMessage()


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        users = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 50)
            )
        ).scalars().all()
        if users:
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(users)))
            ).scalars().all()
            tickets = (
                await session.execute(
                    select(SupportTicket.id).where(SupportTicket.user_id.in_(users))
                )
            ).scalars().all()
            if tickets:
                await session.execute(
                    delete(SupportMessage).where(SupportMessage.ticket_id.in_(tickets))
                )
                await session.execute(delete(SupportTicket).where(SupportTicket.id.in_(tickets)))
            if startups:
                await session.execute(
                    delete(ContactRequest).where(ContactRequest.startup_id.in_(startups))
                )
                await session.execute(
                    delete(StartupReaction).where(StartupReaction.startup_id.in_(startups))
                )
                await session.execute(
                    delete(StartupMedia).where(StartupMedia.startup_id.in_(startups))
                )
                await session.execute(
                    delete(UserCollectionItem).where(UserCollectionItem.startup_id.in_(startups))
                )
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(
                delete(UserCollection).where(UserCollection.user_id.in_(users))
            )
            await session.execute(delete(User).where(User.id.in_(users)))
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
        owner = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="ux_owner",
            first_name="Ольга",
            last_name="Основатель",
            language_code="ru",
        )
        viewer = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 2,
            username="ux_viewer",
            first_name="Виктор",
            last_name="Зритель",
            language_code="ru",
        )
        await ConsentService(session).accept_all(
            viewer.id
        )
        startup = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            slug=f"ux-audit-{uuid.uuid4().hex[:8]}",
            name="UX Audit Startup",
            one_liner="Проект для проверки аудита интерфейса",
            review_state="approved",
            current_revision=1,
            published_at=datetime.now(timezone.utc),
        )
        session.add(startup)
        await session.commit()
        return owner.id, viewer.id, startup.id


async def test_support_categories_are_buttons_not_codes(factory) -> None:  # noqa: ANN001
    keyboard = support_router._categories_keyboard()
    flat = [button for row in keyboard.inline_keyboard for button in row]
    labels = [button.text for button in flat]
    assert "Аккаунт и доступ" in labels
    for text in labels:
        assert "(" not in text and "_" not in text
    assert all(len(button.callback_data.encode()) <= 64 for button in flat)
    assert any("Мои обращения" in text for text in labels)


async def test_support_ticket_list_shows_human_status(factory) -> None:  # noqa: ANN001
    owner_id, _viewer_id, _startup_id = await _seed(factory)
    async with factory() as session:
        ticket = SupportTicket(
            id=uuid.uuid4(),
            public_number="SUP-990001",
            user_id=owner_id,
            subject="Проблема с анкетой",
            status="waiting_user",
            priority="normal",
        )
        session.add(ticket)
        await session.commit()
        text = support_router._tickets_text([ticket])
        keyboard = support_router._tickets_keyboard([ticket])
    assert "Ожидает уточнения" in text
    assert "waiting_user" not in text
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert all("waiting_user" not in button.text for button in buttons)


async def test_support_category_callback_starts_subject_step(factory) -> None:  # noqa: ANN001
    state = FakeState()
    callback = FakeCallback("sup:cat:startup")
    await support_router.on_category_cb(callback, state)
    callback.answer.assert_awaited()
    assert state.data["category"] == "startup"
    state.set_state.assert_awaited()
    prompt = callback.message.answer.await_args.args[0]
    assert "Тема" in prompt
    assert "startup" not in prompt


async def test_complaint_targets_are_named_buttons_without_uuids(factory) -> None:  # noqa: ANN001
    _owner_id, viewer_id, startup_id = await _seed(factory)
    keyboard = complaints_router._types_keyboard()
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "🚀 Стартап" in labels
    assert any("Отмена" in text for text in labels)

    state = FakeState()
    state.data["target_type"] = "startup"
    message = FakeMessage()
    message.text = "UX Audit"
    async with factory() as session:
        await complaints_router.on_search(message, state, session, await session.get(User, viewer_id))
    text = message.answer.await_args.args[0]
    assert "Выберите объект жалобы" in text
    picker = message.answer.await_args.kwargs["reply_markup"]
    flat = [button for row in picker.inline_keyboard for button in row]
    assert all(len(button.text) <= 60 for button in flat)
    assert all(len(button.callback_data.encode()) <= 64 for button in flat)
    assert "UX Audit Startup" in [button.text for button in flat]
    assert state.data["candidates"] == [str(startup_id)]


async def test_profile_favorites_view_lists_saved_startup(factory) -> None:  # noqa: ANN001
    _owner_id, viewer_id, startup_id = await _seed(factory)
    async with factory() as session:
        await feed_service.toggle_favorite(session, user_id=viewer_id, startup_id=startup_id)
        await session.commit()
        items = await profile_router._favorites(session, viewer_id)
    assert [str(item.id) for item in items] == [str(startup_id)]
    text = profile_router._favorites_text(items)
    assert "UX Audit Startup" in text
    assert "🔖" in text


async def test_requests_screen_shows_incoming_and_outgoing(factory) -> None:  # noqa: ANN001
    owner_id, viewer_id, startup_id = await _seed(factory)
    async with factory() as session:
        await contacts.create_request(
            session, requester_id=viewer_id, startup_id=startup_id, message="Хочу обсудить пилот"
        )
        await session.commit()
        incoming = await contacts.requests_for_owner(session, owner_id)
        outgoing = await contacts.requests_for_requester(session, viewer_id)
    text = profile_router._requests_text(incoming, outgoing)
    assert "Входящие" in text and "Исходящие" in text
    assert "ждёт ответа" in text
    assert "pending" not in text
    keyboard = profile_router._requests_keyboard(incoming)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    joined = " ".join(labels)
    assert "UX Audit Startup" in joined
    # No 8-character UUID fragments in user-facing button labels.
    assert not any(len(part) == 8 and part.isalnum() for part in joined.split())


async def test_requests_command_switches_to_menu_keyboard(factory) -> None:  # noqa: ANN001
    _owner_id, viewer_id, _startup_id = await _seed(factory)
    message = FakeMessage()
    state = FakeState()
    async with factory() as session:
        user = await session.get(User, viewer_id)
        await profile_router.requests_command(message, session, user, state)
    assert message.answer.await_count >= 1
    first = message.answer.await_args_list[0]
    assert first.kwargs["reply_markup"] is not None
    assert first.kwargs["reply_markup"].keyboard[0][0].text


async def test_feed_empty_state_does_not_offer_rating(factory) -> None:  # noqa: ANN001
    _owner_id, viewer_id, _startup_id = await _seed(factory)
    from raundo.routers import feed as feed_router

    state = FakeState()
    message = FakeMessage()
    async with factory() as session:
        user = await session.get(User, viewer_id)
        # No approved startups are visible: the single one belongs to the viewer.
        await feed_router._send_card(message, session, user, state, None)
    text = message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert "Пока новых стартапов нет" in text
    assert markup.keyboard[0][0].text == "🚀 Оценивать"
    assert all(button.text != "❤️" for row in markup.keyboard for button in row)
