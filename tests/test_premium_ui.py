"""Dispatcher.feed_update: /premium navigation, payment-disabled, dev-grant gates."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.app import setup_dispatcher
from raundo.catalog_seed import ensure_catalog_seeded
from raundo.config import Settings
from raundo.db.base import Base
from raundo.models.billing import PremiumSubscription
from raundo.models.user import User as DbUser
from raundo.services.age import confirm_age
from raundo.services.consents import ConsentService
from tests.fakes import FakeRedis, RecordingSession, sent_texts

TELEGRAM_ID = 424242
ADMIN_ID = 424242
POLICY = "2026-10-22-draft"


def _tg_user(tid: int = TELEGRAM_ID) -> User:
    return User(id=tid, is_bot=False, first_name="Tester", username=f"u{tid}", language_code="ru")


def _private_chat(tid: int = TELEGRAM_ID) -> Chat:
    return Chat(id=tid, type="private", first_name="Tester")


def _message(*, text: str | None = None, message_id: int = 1, tid: int = TELEGRAM_ID) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=_private_chat(tid),
        from_user=_tg_user(tid),
        text=text,
    )


def _callback(*, data: str, message: Message, callback_id: str = "cb1", tid: int = TELEGRAM_ID) -> CallbackQuery:
    return CallbackQuery(
        id=callback_id,
        from_user=_tg_user(tid),
        chat_instance="chat-instance",
        data=data,
        message=message,
    )


def _edit_texts(recording: RecordingSession) -> list[str]:
    return [
        str(call.text)
        for call in recording.calls
        if call.__api_method__ == "editMessageText" and getattr(call, "text", None)
    ]


def _answers(recording: RecordingSession) -> list[str]:
    return [
        str(call.text)
        for call in recording.calls
        if call.__api_method__ == "answerCallbackQuery" and getattr(call, "text", None)
    ]


@pytest_asyncio.fixture
async def premium_env(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
        ADMIN_TELEGRAM_IDS=str(ADMIN_ID),
    )
    for module in (
        "raundo.routers.common",
        "raundo.routers.admin",
        "raundo.routers.startup",
        "raundo.routers.investor",
        "raundo.routers.premium",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    get_settings = __import__("raundo.config", fromlist=["get_settings"]).get_settings
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        await ensure_catalog_seeded(session)
        await session.commit()

    recording = RecordingSession()
    bot = Bot(token="123456789:AATestTokenForPremiumUITests_Only", session=recording)
    dispatcher = setup_dispatcher(
        storage=MemoryStorage(),
        session_factory=session_factory,
        redis=FakeRedis(),  # type: ignore[arg-type]
        rate_limit=200,
        rate_limit_window_seconds=60,
    )

    async def feed(update: Update) -> None:
        await dispatcher.feed_update(bot, update)

    async def accept_consent() -> None:
        await feed(Update(update_id=1, message=_message(text="/start")))
        async with session_factory() as session:
            user = (
                await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
            ).scalar_one()
            await ConsentService(session).accept_required(user.id, source="test")
            confirm_age(user, "18_plus")
            await session.commit()
        recording.calls.clear()

    yield {
        "feed": feed,
        "session_factory": session_factory,
        "recording": recording,
        "settings": settings,
        "accept_consent": accept_consent,
    }

    await bot.session.close()
    await engine.dispose()
    get_settings.cache_clear()


async def _open_premium(env, mid: int = 10) -> int:
    feed, recording = env["feed"], env["recording"]
    recording.calls.clear()
    await feed(Update(update_id=mid, message=_message(text="/premium", message_id=mid)))
    return mid + 1


async def _tap(env, *, data: str, mid: int, cb_id: str = "cbx") -> None:
    env["recording"].calls.clear()
    await env["feed"](
        Update(
            update_id=mid,
            callback_query=_callback(
                data=data,
                message=_message(text="premium", message_id=mid),
                callback_id=cb_id,
            ),
        )
    )


@pytest.mark.asyncio
async def test_purchase_requires_18_plus(premium_env) -> None:
    env = premium_env
    feed, recording = env["feed"], env["recording"]
    await feed(Update(update_id=1, message=_message(text="/start")))
    async with env["session_factory"]() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        await ConsentService(session).accept_required(user.id, source="test")
        await session.commit()
    recording.calls.clear()
    await _open_premium(env)
    await _tap(env, data="p:buy:community_plus:7d", mid=21)
    texts = "\n".join(_edit_texts(recording) + sent_texts(recording))
    assert "18+" in texts


@pytest.mark.asyncio
async def test_minor_cannot_start_purchase(premium_env) -> None:
    env = premium_env
    feed, recording = env["feed"], env["recording"]
    await feed(Update(update_id=1, message=_message(text="/start")))
    async with env["session_factory"]() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        await ConsentService(session).accept_required(user.id, source="test")
        confirm_age(user, "14_17")
        await session.commit()
    recording.calls.clear()
    await _open_premium(env)
    await _tap(env, data="p:buy:startup_pro:3d", mid=22)
    texts = "\n".join(sent_texts(recording) + _edit_texts(recording))
    assert "18+" in texts
    assert "Банковская карта" not in texts


@pytest.mark.asyncio
async def test_premium_command_shows_full_catalog(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    texts = sent_texts(env["recording"])
    assert any("Premium RAUNDO" in t for t in texts)
    joined = "\n".join(texts)
    for title in ("Community PLUS", "Startup PRO", "Investor PRO", "Organization PRO"):
        assert title in joined
    # Default: no dev-grant button (flag off).
    assert not any("Тестовая выдача" in t for t in texts)


@pytest.mark.asyncio
async def test_premium_requires_consent_first(premium_env) -> None:
    env = premium_env
    await env["feed"](Update(update_id=1, message=_message(text="/premium", message_id=1)))
    texts = sent_texts(env["recording"])
    assert any("примите условия" in t.lower() for t in texts)
    assert not any("Premium RAUNDO" in t for t in texts)


@pytest.mark.asyncio
async def test_plan_screen_shows_all_three_prices(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:plan:organization_pro", mid=20)
    edits = _edit_texts(env["recording"])
    joined = "\n".join(edits)
    assert "Organization PRO" in joined
    for price in ("1 490 ₽", "2 990 ₽", "9 990 ₽"):
        assert price in joined


@pytest.mark.asyncio
async def test_buy_screen_explains_payment_unavailable_without_activation(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:buy:community_plus:7d", mid=21)
    edits = _edit_texts(env["recording"])
    joined = "\n".join(edits)
    assert "Community PLUS" in joined
    assert "99 ₽" in joined
    # Stars disabled in tests and card is never offered inside the bot.
    assert "Оплата временно недоступна" in joined
    async with env["session_factory"]() as session:
        rows = (await session.execute(select(PremiumSubscription))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_unknown_and_stale_callbacks_are_answered_safely(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:plan:ultra_pro", mid=22, cb_id="s1")
    assert any("Неизвестный тариф" in a for a in _answers(env["recording"]))
    await _tap(env, data="p:buy:investor_pro:365d", mid=23, cb_id="s2")
    assert any("Неизвестный тариф или срок" in a for a in _answers(env["recording"]))
    await _tap(env, data="p:root", mid=24, cb_id="s3")
    assert any("Premium RAUNDO" in t for t in _edit_texts(env["recording"]))


@pytest.mark.asyncio
async def test_mine_empty_state(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:mine", mid=25)
    edits = _edit_texts(env["recording"])
    assert any("пока нет подписок" in t for t in edits)


@pytest.mark.asyncio
async def test_dev_grants_disabled_by_default(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:dev", mid=26)
    assert any("Тестовая выдача отключена" in a for a in _answers(env["recording"]))
    async with env["session_factory"]() as session:
        rows = (await session.execute(select(PremiumSubscription))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_dev_grant_requires_admin_even_when_enabled(premium_env) -> None:
    env = premium_env
    env["settings"].premium_dev_grants_enabled = True
    await env["accept_consent"]()
    # Non-admin user taps the grant path directly.
    await env["feed"](
        Update(
            update_id=30,
            callback_query=_callback(
                data="p:devg:investor_pro:30d",
                message=_message(text="premium", message_id=30, tid=777),
                callback_id="na1",
                tid=777,
            ),
        )
    )
    assert any("Тестовая выдача отключена" in a for a in _answers(env["recording"]))
    async with env["session_factory"]() as session:
        rows = (await session.execute(select(PremiumSubscription))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_dev_grant_as_admin_marks_test_and_never_fakes_payment(premium_env) -> None:
    env = premium_env
    env["settings"].premium_dev_grants_enabled = True
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:dev", mid=40)
    edits = _edit_texts(env["recording"])
    assert any("Тестовая выдача" in t for t in edits)
    await _tap(env, data="p:devg:investor_pro:30d", mid=41, cb_id="g1")
    answers = _answers(env["recording"])
    assert any("выдана" in a for a in answers)
    async with env["session_factory"]() as session:
        subs = (await session.execute(select(PremiumSubscription))).scalars().all()
        assert len(subs) == 1
        sub = subs[0]
        assert sub.is_test is True
        assert sub.source == "test_grant"
        assert sub.test_reason is not None
        assert sub.granted_by_telegram_id == ADMIN_ID
    # The recorded Bot API calls contain no invoice or payment imitation.
    methods = [call.__api_method__ for call in env["recording"].calls]
    assert "sendInvoice" not in methods


@pytest.mark.asyncio
async def test_mine_lists_active_subscription_after_dev_grant(premium_env) -> None:
    env = premium_env
    env["settings"].premium_dev_grants_enabled = True
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:devg:community_plus:3d", mid=50, cb_id="g2")
    edits = _edit_texts(env["recording"])
    joined = "\n".join(edits)
    assert "Мои подписки" in joined
    assert "Community PLUS" in joined
    assert "тестовая" in joined


@pytest.mark.asyncio
async def test_free_core_still_works_alongside_premium(premium_env) -> None:
    env = premium_env
    await env["accept_consent"]()
    await env["feed"](Update(update_id=60, message=_message(text="/rate", message_id=60)))
    texts = sent_texts(env["recording"])
    assert any("лента" in t.lower() or "оценивать" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_card_method_is_unavailable_in_demo_mode(premium_env) -> None:
    from raundo.models.payments import PaymentOrder

    env = premium_env
    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:buy:community_plus:7d", mid=30)
    method_edit = next(
        call
        for call in env["recording"].calls
        if call.__api_method__ == "editMessageText"
    )
    labels = [
        button.text
        for row in method_edit.reply_markup.inline_keyboard
        for button in row
    ]
    assert not any("карта" in label.lower() or "СБП" in label for label in labels)
    assert not any("Telegram Stars" in label for label in labels)
    assert "Оплата временно недоступна" in str(method_edit.text)

    env["recording"].calls.clear()
    await _tap(env, data="p:m:card", mid=31)
    assert any("картой" in answer for answer in _answers(env["recording"]))

    async with env["session_factory"]() as session:
        orders = (await session.execute(select(PaymentOrder))).scalars().all()
        assert orders == []
        subs = (await session.execute(select(PremiumSubscription))).scalars().all()
        assert subs == []


@pytest.mark.asyncio
async def test_stars_flow_requires_offer_confirmation_before_invoice(premium_env) -> None:
    from raundo.models.payments import PaymentOrder

    env = premium_env
    await env["accept_consent"]()
    env["settings"].payments_stars_enabled = True
    env["settings"].payments_stars_prices_approved = True
    env["settings"].payments_legal_ready = True

    await _open_premium(env)
    await _tap(env, data="p:buy:community_plus:7d", mid=70)

    env["recording"].calls.clear()
    await _tap(env, data="p:m:stars", mid=71)
    confirm_edits = "\\n".join(_edit_texts(env["recording"]))
    assert "принимаете оферту" in confirm_edits
    assert "100" in confirm_edits
    assert not any(
        call.__api_method__ == "sendInvoice" for call in env["recording"].calls
    )

    env["recording"].calls.clear()
    await _tap(env, data="p:sg:community_plus:7d", mid=72)
    invoices = [call for call in env["recording"].calls if call.__api_method__ == "sendInvoice"]
    assert len(invoices) == 1
    invoice = invoices[0]
    assert invoice.currency == "XTR"
    assert invoice.prices[0].amount == 100
    async with env["session_factory"]() as session:
        order = await session.scalar(select(PaymentOrder))
        assert order is not None
        assert order.payment_provider == "stars"
        assert order.currency == "XTR"
        assert order.amount_minor == 100
        assert str(order.id) == invoice.payload

@pytest.mark.asyncio
async def test_live_method_screen_offers_stars_and_card(premium_env) -> None:  # noqa: ANN001
    env = premium_env
    settings = env["settings"]
    settings.payments_mode = "live"
    settings.payments_stars_enabled = True
    settings.payments_stars_prices_approved = True
    settings.payments_legal_ready = True
    settings.yookassa_shop_id = "1517068"
    settings.yookassa_secret_key = "test-key"

    await env["accept_consent"]()
    await _open_premium(env)
    await _tap(env, data="p:buy:community_plus:7d", mid=30)
    method_edit = next(
        call
        for call in env["recording"].calls
        if call.__api_method__ == "editMessageText"
    )
    labels = [
        button.text
        for row in method_edit.reply_markup.inline_keyboard
        for button in row
    ]
    assert any("Telegram Stars" in label for label in labels)
    assert any("карта" in label.lower() or "СБП" in label for label in labels)
