"""Integration: real Update path via Dispatcher.feed_update (not direct handlers)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.app import setup_dispatcher
from raundo.config import Settings, get_settings
from raundo.db.base import Base
from raundo.keyboards import CALLBACK_ACCEPT_CONSENT
from raundo.models.user import User as DbUser
from raundo.models.user import UserConsent
from raundo.services.age import confirm_age
from raundo.services.consents import ConsentService
from tests.fakes import FakeRedis, RecordingSession, sent_texts

TELEGRAM_ID = 424242
POLICY = "2026-10-22-draft"


def _tg_user() -> User:
    return User(
        id=TELEGRAM_ID,
        is_bot=False,
        first_name="Tester",
        username="tester_raundo",
        language_code="ru",
    )


def _private_chat() -> Chat:
    return Chat(id=TELEGRAM_ID, type="private", first_name="Tester")


def _message(*, text: str, message_id: int = 1) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=_private_chat(),
        from_user=_tg_user(),
        text=text,
    )



async def _seed_onboarding(session_factory) -> None:  # noqa: ANN001
    """Grant required consents and 18+ directly in the DB for non-consent tests."""
    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        await ConsentService(session).accept_required(user.id, source="test")
        confirm_age(user, "18_plus")
        await session.commit()
def _callback(*, data: str, message: Message, callback_id: str = "cb1") -> CallbackQuery:
    return CallbackQuery(
        id=callback_id,
        from_user=_tg_user(),
        chat_instance="chat-instance",
        data=data,
        message=message,
    )


@pytest_asyncio.fixture
async def feed_env(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
    )
    monkeypatch.setattr("raundo.routers.common.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.startup.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.investor.get_settings", lambda: settings)
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    recording = RecordingSession()
    bot = Bot(token="123456789:AATestTokenForFeedUpdateTests_Only", session=recording)
    redis = FakeRedis()
    dispatcher = setup_dispatcher(
        storage=MemoryStorage(),
        session_factory=session_factory,
        redis=redis,  # type: ignore[arg-type]
        rate_limit=10,
        rate_limit_window_seconds=60,
    )

    async def feed(update: Update) -> None:
        await dispatcher.feed_update(bot, update)

    yield {
        "feed": feed,
        "session_factory": session_factory,
        "recording": recording,
        "redis": redis,
        "bot": bot,
        "dispatcher": dispatcher,
    }

    await bot.session.close()
    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_support_reachable_without_consents(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))
    recording.calls.clear()
    await feed(Update(update_id=2, message=_message(text="/support", message_id=2)))

    texts = sent_texts(recording)
    assert any("поддержка" in t.lower() for t in texts)

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        assert not await ConsentService(session).has_required_consents(user.id)


@pytest.mark.asyncio
async def test_data_consent_can_be_revoked_from_settings(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))
    await _seed_onboarding(session_factory)
    recording.calls.clear()

    await feed(Update(update_id=5, message=_message(text="/settings", message_id=5)))
    await feed(
        Update(
            update_id=6,
            callback_query=_callback(
                data="consent:revoke:data_processing",
                message=_message(text="c", message_id=6),
                callback_id="rev-prompt",
            ),
        )
    )
    prompt_texts = [
        str(getattr(call, "text", "")) for call in recording.calls if getattr(call, "text", None)
    ]
    assert any("отзыв" in text.lower() for text in prompt_texts)

    await feed(
        Update(
            update_id=7,
            callback_query=_callback(
                data="consent:data:confirm",
                message=_message(text="c", message_id=7),
                callback_id="rev-confirm",
            ),
        )
    )

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        service = ConsentService(session)
        assert not await service.has_required_consents(user.id)
        history = await service.history(user.id)
        data_rows = [row for row in history if row.kind == "data_processing"]
        assert data_rows and data_rows[0].revoked_at is not None
        assert data_rows[0].revoke_reason == "user_request"

    texts = sent_texts(recording)
    assert any("отозвано" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_delete_account_reachable_without_consents(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))
    recording.calls.clear()
    await feed(Update(update_id=2, message=_message(text="/delete", message_id=2)))
    texts = sent_texts(recording)
    assert any("удаление аккаунта" in t.lower() for t in texts)

    await feed(
        Update(
            update_id=3,
            callback_query=_callback(
                data="account:delete:confirm", message=_message(text="a", message_id=3)
            ),
        )
    )

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        assert user.deleted_at is not None
        assert user.anonymized_at is not None
        assert user.username is None


@pytest.mark.asyncio
async def test_feed_start_binds_db_user_and_asks_consent(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        assert user.username == "tester_raundo"
        consents = ConsentService(session)
        assert not await consents.has_required_consents(user.id)

    texts = sent_texts(recording)
    assert any("подтвердите согласие" in t.lower() or "соглас" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_feed_accept_consent_persists_then_welcomes(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))
    prompt_message = _message(text="consent prompt", message_id=10)
    # Nothing is pre-selected: the age button must not finish onboarding yet.
    await feed(
        Update(
            update_id=2,
            callback_query=_callback(data="age:18_plus", message=prompt_message),
        )
    )
    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        assert not await ConsentService(session).has_required_consents(user.id)
        assert user.age_bracket is None

    await feed(
        Update(
            update_id=3,
            callback_query=_callback(data="onb:consent", message=prompt_message),
        )
    )
    await feed(
        Update(
            update_id=4,
            callback_query=_callback(data="age:18_plus", message=prompt_message),
        )
    )

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        consents = ConsentService(session)
        assert await consents.has_required_consents(user.id)
        assert user.age_bracket == "18_plus"
        count = (
            await session.execute(
                select(func.count()).select_from(UserConsent).where(UserConsent.user_id == user.id)
            )
        ).scalar_one()
        assert count == 3

    texts = sent_texts(recording)
    assert any("стартапы встречают инвесторов" in t.lower() for t in texts)

    recording.calls.clear()
    await feed(Update(update_id=7, message=_message(text="/start", message_id=3)))
    texts_again = sent_texts(recording)
    assert any("стартапы встречают инвесторов" in t.lower() for t in texts_again)
    assert not any("подтвердите согласие" in t.lower() for t in texts_again)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("🆘 Поддержка", "Поддержка RAUNDO"),
        ("🔖 Избранное", "Избранное"),
        ("🤝 Запросы", "Запросы на знакомство"),
        ("⚙️ Настройки", "Настройки"),
        ("👤 Мой профиль", "Мой профиль"),
        ("🏆 Топ-10", "Топ-10"),
        ("📝 Мой стартап", "Мои проекты"),
        ("💼 Инвесторам", "Мои инвесторские профили"),
        ("⭐ Premium", "Premium RAUNDO"),
    ],
)
@pytest.mark.asyncio
async def test_menu_buttons_reach_real_sections(feed_env, label: str, expected: str) -> None:
    """Every bottom-menu label must open its section, not the fallback answer.

    This is the regression for «🆘 Поддержка»: the router compared the text
    without the emoji, so the button answered «Не понял сообщение».
    """
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))
    await _seed_onboarding(feed_env["session_factory"])
    recording.calls.clear()
    await feed(Update(update_id=3, message=_message(text=label, message_id=3)))
    texts = sent_texts(recording)
    assert any(expected in text for text in texts), (label, texts)
    assert not any("Не понял сообщение" in text for text in texts), (label, texts)


@pytest.mark.asyncio
async def test_feed_consent_db_failure_does_not_ack_success(
    feed_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_message(text="/start")))

    async def boom(self, user_id, **kwargs):  # noqa: ANN001
        raise RuntimeError("forced consent failure")

    monkeypatch.setattr(ConsentService, "accept_required", boom)

    prompt_message = _message(text="consent prompt", message_id=10)
    await feed(
        Update(
            update_id=2,
            callback_query=_callback(data="onb:consent", message=prompt_message),
        )
    )
    await feed(
        Update(
            update_id=3,
            callback_query=_callback(data="age:18_plus", message=prompt_message),
        )
    )

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        consents = ConsentService(session)
        assert not await consents.has_required_consents(user.id)

    texts = sent_texts(recording)
    assert any("не удалось сохранить согласие" in t.lower() for t in texts)
    assert not any(t.lower() == "согласие принято" for t in texts)


@pytest.mark.asyncio
async def test_feed_rate_limit_blocks_after_window(feed_env) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    redis: FakeRedis = feed_env["redis"]

    # limit=10 in fixture: the 11th update is blocked by RateLimitMiddleware
    for update_id in range(1, 11):
        await feed(
            Update(update_id=update_id, message=_message(text="/help", message_id=update_id))
        )
    recording.calls.clear()
    await feed(Update(update_id=99, message=_message(text="/help", message_id=99)))

    texts = sent_texts(recording)
    assert any("слишком много запросов" in t.lower() for t in texts)
    assert redis.counts.get(f"raundo:rl:{TELEGRAM_ID}", 0) >= 11


def _group_message(*, text: str, message_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100123, type="supergroup", title="Group"),
        from_user=_tg_user(),
        text=text,
    )


@pytest.mark.asyncio
async def test_feed_group_chat_is_ignored(feed_env) -> None:
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=1, message=_group_message(text="/start", message_id=1)))

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one_or_none()
        assert user is None
    assert sent_texts(recording) == []


@pytest.mark.asyncio
async def test_feed_group_over_rate_limit_never_replies_or_inserts(feed_env) -> None:
    """P1 regression: rate limiter must not answer in groups after exceeding limit."""
    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]
    redis: FakeRedis = feed_env["redis"]

    # Fixture limit=3; send well above the cap including a group callback.
    for update_id in range(1, 8):
        await feed(
            Update(
                update_id=update_id,
                message=_group_message(text="/start", message_id=update_id),
            )
        )

    group_msg = _group_message(text="prompt", message_id=50)
    await feed(
        Update(
            update_id=50,
            callback_query=CallbackQuery(
                id="group-cb",
                from_user=_tg_user(),
                chat_instance="group-ci",
                data=CALLBACK_ACCEPT_CONSENT,
                message=group_msg,
            ),
        )
    )

    assert recording.calls == []
    assert sent_texts(recording) == []
    assert redis.counts.get(f"raundo:rl:{TELEGRAM_ID}") is None

    async with session_factory() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one_or_none()
        assert user is None
        total_users = (await session.execute(select(func.count()).select_from(DbUser))).scalar_one()
        assert total_users == 0


@pytest.mark.asyncio
async def test_feed_duplicate_update_id_is_skipped_once(feed_env) -> None:
    """Regression: processed_updates ledger must make replay a no-op."""
    from raundo.models.system import ProcessedUpdate

    feed = feed_env["feed"]
    session_factory = feed_env["session_factory"]
    recording: RecordingSession = feed_env["recording"]

    await feed(Update(update_id=501, message=_message(text="/start", message_id=501)))
    texts_first = sent_texts(recording)
    assert texts_first
    recording.calls.clear()

    # Same update_id replayed (Telegram retry / double delivery)
    await feed(Update(update_id=501, message=_message(text="/start", message_id=501)))
    assert sent_texts(recording) == []

    async with session_factory() as session:
        row = await session.get(ProcessedUpdate, 501)
        assert row is not None
        users = (
            await session.execute(select(func.count()).select_from(DbUser))
        ).scalar_one()
        assert users == 1


@pytest.mark.asyncio
async def test_feed_admin_denied_when_allowlist_empty(feed_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live default: empty ADMIN_TELEGRAM_IDS → every /admin is denied."""
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
        ADMIN_TELEGRAM_IDS="",
    )
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.common.get_settings", lambda: settings)

    await feed(Update(update_id=1, message=_message(text="/start")))
    prompt = _message(text="consent", message_id=10)
    for offset, kind in enumerate(("terms", "privacy", "data_processing"), start=2):
        await feed(
            Update(
                update_id=offset,
                callback_query=_callback(data=f"consent:t:{kind}", message=prompt),
            )
        )
    await feed(
        Update(
            update_id=5,
            callback_query=_callback(data="consent:accept", message=prompt),
        )
    )
    await feed(
        Update(update_id=6, callback_query=_callback(data="age:18_plus", message=prompt))
    )
    recording.calls.clear()
    await feed(Update(update_id=12, message=_message(text="/admin", message_id=12)))
    texts = sent_texts(recording)
    assert any("запрещён" in t.lower() for t in texts)


