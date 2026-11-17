"""Dispatcher.feed_update: /admin routing, wizard paths, ownership isolation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PhotoSize,
    Update,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.app import setup_dispatcher
from raundo.catalog_seed import ensure_catalog_seeded
from raundo.config import Settings, get_settings
from raundo.db.base import Base
from raundo.domain import REVIEW_SUBMITTED
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup
from raundo.models.system import OutboxEvent
from raundo.models.user import User as DbUser
from raundo.services.age import confirm_age
from raundo.services.consents import ConsentService
from raundo.wizard_keyboards import (
    BTN_BACK,
    BTN_CONTINUE_FREE,
    BTN_CREATE_INVESTOR,
    BTN_CREATE_STARTUP,
    BTN_PHOTOS_DONE,
    BTN_SKIP,
    BTN_SUBMIT,
    CB_AUD,
    CB_CONTACT,
    CB_DONE_AUD,
    CB_DONE_INT,
    CB_DONE_PROG,
    CB_DONE_SEC,
    CB_DONE_SM,
    CB_DONE_STG,
    CB_FUND,
    CB_GEO,
    CB_INTENT,
    CB_ITYPE,
    CB_MON,
    CB_PROG,
    CB_SEC,
    CB_SMOC,
    CB_STG,
)
from tests.fakes import FakeRedis, RecordingSession, sent_texts

TELEGRAM_ID = 424242
ADMIN_ID = 424242
POLICY = "2026-10-22-draft"


def _tg_user(tid: int = TELEGRAM_ID) -> User:
    return User(
        id=tid,
        is_bot=False,
        first_name="Tester",
        username=f"u{tid}",
        language_code="ru",
    )


def _private_chat(tid: int = TELEGRAM_ID) -> Chat:
    return Chat(id=tid, type="private", first_name="Tester")


async def _feed_startup_after_intents(feed, *, mid: int, cb_prefix: str = "x") -> int:
    """Audience → progress → monetization → skip ask → not_raising → geo. Returns next mid."""
    prompt = _message(text="aud", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_AUD}b2c", message=prompt, callback_id=f"{cb_prefix}a1"
            ),
        )
    )
    mid += 1
    prompt = _message(text="audd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=CB_DONE_AUD, message=prompt, callback_id=f"{cb_prefix}a2"
            ),
        )
    )
    mid += 1
    prompt = _message(text="prg", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_PROG}mvp", message=prompt, callback_id=f"{cb_prefix}p1"
            ),
        )
    )
    mid += 1
    prompt = _message(text="prgd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=CB_DONE_PROG, message=prompt, callback_id=f"{cb_prefix}p2"
            ),
        )
    )
    mid += 1
    prompt = _message(text="mon", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_MON}not_yet", message=prompt, callback_id=f"{cb_prefix}m1"
            ),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SKIP, message_id=mid)))
    mid += 1
    prompt = _message(text="fund", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_FUND}not_raising", message=prompt, callback_id=f"{cb_prefix}f1"
            ),
        )
    )
    mid += 1
    prompt = _message(text="geo", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_GEO}ru", message=prompt, callback_id=f"{cb_prefix}g1"
            ),
        )
    )
    return mid + 1


def _message(*, text: str | None = None, message_id: int = 1, tid: int = TELEGRAM_ID, photo=None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=_private_chat(tid),
        from_user=_tg_user(tid),
        text=text,
        photo=photo,
    )


def _callback(*, data: str, message: Message, callback_id: str = "cb1", tid: int = TELEGRAM_ID) -> CallbackQuery:
    return CallbackQuery(
        id=callback_id,
        from_user=_tg_user(tid),
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
        ADMIN_TELEGRAM_IDS=str(ADMIN_ID),
    )
    monkeypatch.setattr("raundo.routers.common.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.startup.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.investor.get_settings", lambda: settings)
    monkeypatch.setattr("raundo.routers.premium.get_settings", lambda: settings)
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        await ensure_catalog_seeded(session)
        await session.commit()

    recording = RecordingSession()
    bot = Bot(token="123456789:AATestTokenForFeedUpdateTests_Only", session=recording)
    redis = FakeRedis()
    dispatcher = setup_dispatcher(
        storage=MemoryStorage(),
        session_factory=session_factory,
        redis=redis,  # type: ignore[arg-type]
        rate_limit=200,
        rate_limit_window_seconds=60,
    )
    async def feed(update: Update) -> None:
        await dispatcher.feed_update(bot, update)

    yield {
        "feed": feed,
        "session_factory": session_factory,
        "recording": recording,
        "bot": bot,
        "dispatcher": dispatcher,
    }

    await bot.session.close()
    await engine.dispose()
    get_settings.cache_clear()


async def _accept_consent(feed_env) -> None:  # noqa: ANN001
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    await feed(Update(update_id=1, message=_message(text="/start")))
    async with feed_env["session_factory"]() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        service = ConsentService(session)
        await service.accept_required(user.id, source="test")
        await service.grant(user.id, "profile_publication", source="test")
        confirm_age(user, "18_plus")
        await session.commit()
    recording.calls.clear()


@pytest.mark.asyncio
async def test_minor_cannot_open_investor_section(feed_env) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    await feed(Update(update_id=1, message=_message(text="/start")))
    async with feed_env["session_factory"]() as session:
        user = (
            await session.execute(select(DbUser).where(DbUser.telegram_id == TELEGRAM_ID))
        ).scalar_one()
        await ConsentService(session).accept_required(user.id, source="test")
        confirm_age(user, "14_17")
        await session.commit()
    recording.calls.clear()
    await feed(Update(update_id=5, message=_message(text="/investor", message_id=5)))
    texts = sent_texts(recording)
    assert any("18+" in t for t in texts)
    assert not any("инвесторские профили" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_feed_admin_authorized_reaches_handler(feed_env) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    await _accept_consent(feed_env)

    await feed(Update(update_id=10, message=_message(text="/admin", message_id=20)))
    texts = sent_texts(recording)
    assert any("панель владельца" in t.lower() or "issue #8" in t.lower() for t in texts)
    assert not any("запрещён" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_feed_admin_unauthorized_denied(feed_env, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
        ADMIN_TELEGRAM_IDS="111",
    )
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)

    await _accept_consent(feed_env)
    await feed(Update(update_id=11, message=_message(text="/admin", message_id=21)))
    texts = sent_texts(recording)
    assert any("запрещён" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_feed_version_authorized_shows_runtime(
    feed_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
        ADMIN_TELEGRAM_IDS=str(ADMIN_ID),
    )
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)
    await _accept_consent(feed_env)
    await feed(Update(update_id=13, message=_message(text="/version", message_id=23)))
    texts = sent_texts(recording)
    assert any("RAUNDO" in t and "PID:" in t for t in texts)
    assert not any("BOT_TOKEN" in t or "DATABASE_URL" in t for t in texts)


@pytest.mark.asyncio
async def test_feed_version_unauthorized_denied(
    feed_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    settings = Settings(
        _env_file=None,
        POLICY_VERSION=POLICY,
        BOT_TOKEN="1:TEST",
        ADMIN_TELEGRAM_IDS="111",
    )
    monkeypatch.setattr("raundo.routers.admin.get_settings", lambda: settings)
    await _accept_consent(feed_env)
    await feed(Update(update_id=14, message=_message(text="/version", message_id=24)))
    texts = sent_texts(recording)
    assert any("запрещён" in t.lower() for t in texts)


@pytest.mark.asyncio
async def test_feed_startup_wizard_photo_back_resume_submit(feed_env) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)

    mid = 100
    await feed(Update(update_id=mid, message=_message(text="/startup", message_id=mid)))
    mid += 1
    await feed(
        Update(update_id=mid, message=_message(text=BTN_CREATE_STARTUP, message_id=mid))
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="RaundoApp", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="x" * 50, message_id=mid)))
    # Back from pitch-next (description) → pitch
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_BACK, message_id=mid)))
    texts = sent_texts(recording)
    assert any("одним предложением" in t.lower() or "50" in t for t in texts)
    recording.calls.clear()

    mid += 1
    await feed(Update(update_id=mid, message=_message(text="x" * 50, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="y" * 100, message_id=mid)))

    # primary sector
    mid += 1
    prompt = _message(text="cats", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_SEC}ai_ml", message=prompt, callback_id="s1"),
        )
    )
    # secondary done empty
    mid += 1
    prompt = _message(text="sec", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SEC, message=prompt, callback_id="s2"),
        )
    )
    # stage
    mid += 1
    prompt = _message(text="stg", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_STG}mvp", message=prompt, callback_id="s3"),
        )
    )
    mid += 1
    await feed(
        Update(
            update_id=mid,
            message=_message(text="https://example.com/app", message_id=mid),
        )
    )
    # photo
    photo = [
        PhotoSize(file_id="f1", file_unique_id="u1", width=100, height=100, file_size=10)
    ]
    mid += 1
    await feed(Update(update_id=mid, message=_message(message_id=mid, photo=photo)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_PHOTOS_DONE, message_id=mid)))
    # intent
    mid += 1
    prompt = _message(text="int", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_INTENT}investment", message=prompt, callback_id="i1"),
        )
    )
    mid += 1
    prompt = _message(text="intd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_INT, message=prompt, callback_id="i2"),
        )
    )
    mid = await _feed_startup_after_intents(feed, mid=mid + 1, cb_prefix="s")
    await feed(
        Update(update_id=mid, message=_message(text="Иван | CEO", message_id=mid))
    )
    mid += 1
    prompt = _message(text="ctc", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_CONTACT}relay", message=prompt, callback_id="c1"),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SUBMIT, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CONTINUE_FREE, message_id=mid)))

    async with session_factory() as session:
        startup = (await session.execute(select(Startup))).scalar_one()
        assert startup.review_state == REVIEW_SUBMITTED
        assert startup.name == "RaundoApp"
        assert startup.audience_codes == ["b2c"]
        assert startup.progress_codes == ["mvp"]
        assert startup.monetization_status == "not_yet"
        assert startup.fundraising_stage == "not_raising"
        assert startup.country == "Россия"
        outbox = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.kind == "startup.submitted")
            )
        ).scalar_one()
        assert outbox.dedupe_key.startswith("startup_submitted:")


@pytest.mark.asyncio
async def test_feed_investor_accelerator_no_cheque_and_org_claim(feed_env) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)

    mid = 200
    await feed(Update(update_id=mid, message=_message(text="/investor", message_id=mid)))
    mid += 1
    await feed(
        Update(update_id=mid, message=_message(text=BTN_CREATE_INVESTOR, message_id=mid))
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Anna Accel", message_id=mid)))
    mid += 1
    prompt = _message(text="type", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_ITYPE}accelerator", message=prompt, callback_id="t1"
            ),
        )
    )
    mid += 1
    prompt = _message(text="sm", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_SMOC}program", message=prompt, callback_id="sm1"),
        )
    )
    mid += 1
    prompt = _message(text="smd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SM, message=prompt, callback_id="sm2"),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="RAUNDO Labs", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Program Manager", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="s" * 100, message_id=mid)))
    mid += 1
    prompt = _message(text="sec", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_SEC}ai_ml", message=prompt, callback_id="sec1"),
        )
    )
    mid += 1
    prompt = _message(text="secd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SEC, message=prompt, callback_id="sec2"),
        )
    )
    mid += 1
    prompt = _message(text="stg", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_STG}mvp", message=prompt, callback_id="stg1"),
        )
    )
    mid += 1
    prompt = _message(text="stgd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_STG, message=prompt, callback_id="stg2"),
        )
    )
    # cheque skipped for accelerator/program → geography
    texts = sent_texts(recording)
    assert any("география" in t.lower() for t in texts)
    mid += 1
    prompt = _message(text="geo", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_GEO}ru", message=prompt, callback_id="g1"),
        )
    )
    mid += 1
    await feed(
        Update(
            update_id=mid,
            message=_message(text="https://example.com/accel", message_id=mid),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SKIP, message_id=mid)))
    mid += 1
    prompt = _message(text="ctc", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_CONTACT}relay", message=prompt, callback_id="c1"),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SUBMIT, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CONTINUE_FREE, message_id=mid)))

    async with session_factory() as session:
        profile = (await session.execute(select(InvestorProfile))).scalar_one()
        assert profile.review_status == REVIEW_SUBMITTED
        assert profile.investor_type == "accelerator"
        assert "program" in (profile.support_modes or [])
        assert profile.affiliation_status == "claimed"
        assert profile.claimed_organization_id is not None
        org = await session.get(FundingOrganization, profile.claimed_organization_id)
        assert org is not None
        assert org.review_status == "draft"
        mem = (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == profile.user_id
                )
            )
        ).scalar_one()
        assert mem.status == "claimed"
        assert mem.role == "Program Manager"
        assert profile.position == "Program Manager"


async def _startup_to_preview(feed, recording: RecordingSession, *, mid_start: int = 500) -> int:
    """Drive founder wizard from /startup through contact policy; return next mid."""
    mid = mid_start
    await feed(Update(update_id=mid, message=_message(text="/startup", message_id=mid)))
    mid += 1
    await feed(
        Update(update_id=mid, message=_message(text=BTN_CREATE_STARTUP, message_id=mid))
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="CommitProbe", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="x" * 50, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="y" * 100, message_id=mid)))
    mid += 1
    prompt = _message(text="cats", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_SEC}ai_ml", message=prompt, callback_id="cp1"),
        )
    )
    mid += 1
    prompt = _message(text="sec", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SEC, message=prompt, callback_id="cp2"),
        )
    )
    mid += 1
    prompt = _message(text="stg", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_STG}mvp", message=prompt, callback_id="cp3"),
        )
    )
    mid += 1
    await feed(
        Update(
            update_id=mid,
            message=_message(text="https://example.com/probe", message_id=mid),
        )
    )
    photo = [
        PhotoSize(file_id="fp", file_unique_id="up", width=10, height=10, file_size=1)
    ]
    mid += 1
    await feed(Update(update_id=mid, message=_message(message_id=mid, photo=photo)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_PHOTOS_DONE, message_id=mid)))
    mid += 1
    prompt = _message(text="int", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_INTENT}investment", message=prompt, callback_id="cp4"
            ),
        )
    )
    mid += 1
    prompt = _message(text="intd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_INT, message=prompt, callback_id="cp5"),
        )
    )
    mid = await _feed_startup_after_intents(feed, mid=mid + 1, cb_prefix="cp")
    await feed(Update(update_id=mid, message=_message(text="Иван | CEO", message_id=mid)))
    mid += 1
    prompt = _message(text="ctc", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_CONTACT}relay", message=prompt, callback_id="cp6"
            ),
        )
    )
    recording.calls.clear()
    return mid + 1


@pytest.mark.asyncio
async def test_feed_startup_commit_failure_no_false_success(
    feed_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)
    mid = await _startup_to_preview(feed, recording, mid_start=500)

    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit
    fail_budget = {"n": 1}

    async def flaky_commit(self):  # noqa: ANN001
        if fail_budget["n"] > 0:
            fail_budget["n"] -= 1
            raise RuntimeError("forced commit failure")
        return await real_commit(self)

    await feed(Update(update_id=mid, message=_message(text=BTN_SUBMIT, message_id=mid)))
    mid += 1
    recording.calls.clear()
    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    await feed(Update(update_id=mid, message=_message(text=BTN_CONTINUE_FREE, message_id=mid)))
    texts = sent_texts(recording)
    joined = "\n".join(texts).lower()
    assert "не удалось отправить" in joined
    assert not ("✅" in "\n".join(texts) and "проверку" in joined)

    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    async with session_factory() as session:
        startup = (await session.execute(select(Startup))).scalar_one()
        assert startup.review_state == "draft"
        assert startup.name == "CommitProbe"
        outbox = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.kind == "startup.submitted")
            )
        ).scalar_one_or_none()
        assert outbox is None


async def _angel_investor_to_preview(feed, recording: RecordingSession, *, mid_start: int = 700) -> int:
    """Drive angel investor wizard through contact policy; return next mid."""
    mid = mid_start
    await feed(Update(update_id=mid, message=_message(text="/investor", message_id=mid)))
    mid += 1
    await feed(
        Update(update_id=mid, message=_message(text=BTN_CREATE_INVESTOR, message_id=mid))
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Angel Commit", message_id=mid)))
    mid += 1
    prompt = _message(text="type", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_ITYPE}angel", message=prompt, callback_id="ac1"),
        )
    )
    mid += 1
    prompt = _message(text="sm", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_SMOC}direct_equity", message=prompt, callback_id="ac2"
            ),
        )
    )
    mid += 1
    prompt = _message(text="smd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SM, message=prompt, callback_id="ac3"),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="-", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="s" * 100, message_id=mid)))
    mid += 1
    prompt = _message(text="sec", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_SEC}ai_ml", message=prompt, callback_id="ac4"),
        )
    )
    mid += 1
    prompt = _message(text="secd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SEC, message=prompt, callback_id="ac5"),
        )
    )
    mid += 1
    prompt = _message(text="stg", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_STG}mvp", message=prompt, callback_id="ac6"),
        )
    )
    mid += 1
    prompt = _message(text="stgd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_STG, message=prompt, callback_id="ac7"),
        )
    )
    mid += 1
    from raundo.wizard_keyboards import BTN_CHECK_INDIVIDUAL

    await feed(
        Update(update_id=mid, message=_message(text=BTN_CHECK_INDIVIDUAL, message_id=mid))
    )
    mid += 1
    prompt = _message(text="geo", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_GEO}ru", message=prompt, callback_id="ac8"),
        )
    )
    mid += 1
    await feed(
        Update(
            update_id=mid,
            message=_message(text="https://example.com/angel", message_id=mid),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SKIP, message_id=mid)))
    mid += 1
    prompt = _message(text="ctc", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_CONTACT}relay", message=prompt, callback_id="ac9"
            ),
        )
    )
    recording.calls.clear()
    return mid + 1


@pytest.mark.asyncio
async def test_feed_investor_commit_failure_no_false_success(
    feed_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)
    mid = await _angel_investor_to_preview(feed, recording, mid_start=800)

    from sqlalchemy.ext.asyncio import AsyncSession

    real_commit = AsyncSession.commit
    fail_budget = {"n": 1}

    async def flaky_commit(self):  # noqa: ANN001
        if fail_budget["n"] > 0:
            fail_budget["n"] -= 1
            raise RuntimeError("forced commit failure")
        return await real_commit(self)

    await feed(Update(update_id=mid, message=_message(text=BTN_SUBMIT, message_id=mid)))
    mid += 1
    recording.calls.clear()
    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    await feed(Update(update_id=mid, message=_message(text=BTN_CONTINUE_FREE, message_id=mid)))
    texts = sent_texts(recording)
    joined = "\n".join(texts).lower()
    assert "не удалось отправить" in joined
    assert not ("✅" in "\n".join(texts) and "проверку" in joined)

    monkeypatch.setattr(AsyncSession, "commit", real_commit)

    async with session_factory() as session:
        profile = (await session.execute(select(InvestorProfile))).scalar_one()
        assert profile.review_status == "draft"
        assert profile.display_name == "Angel Commit"
        outbox = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.kind == "investor.submitted")
            )
        ).scalar_one_or_none()
        assert outbox is None


@pytest.mark.asyncio
async def test_feed_invalid_pitch_rejected_keeps_wizard(feed_env) -> None:
    from raundo.domain import PITCH_MIN, validate_pitch
    from raundo.wizard_keyboards import BTN_CREATE_STARTUP

    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    expected_err = validate_pitch("too short")
    assert expected_err is not None and str(PITCH_MIN) in expected_err
    await _accept_consent(feed_env)

    mid = 80
    await feed(Update(update_id=mid, message=_message(text="/startup", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CREATE_STARTUP, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="ShortCo", message_id=mid)))
    recording.calls.clear()
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="too short", message_id=mid)))
    assert expected_err in sent_texts(recording)


@pytest.mark.asyncio
async def test_feed_startup_cancel_clears_fsm_keeps_draft(feed_env) -> None:
    """Cancel stops wizard; draft row remains for resume (no destructive wipe)."""
    from raundo.wizard_keyboards import BTN_CANCEL, BTN_CREATE_STARTUP

    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)

    mid = 900
    await feed(Update(update_id=mid, message=_message(text="/startup", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CREATE_STARTUP, message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="CancelCo", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CANCEL, message_id=mid)))
    texts = sent_texts(recording)
    assert any("прервана" in t.lower() for t in texts)

    async with session_factory() as session:
        startup = (await session.execute(select(Startup))).scalar_one()
        assert startup.name == "CancelCo"
        assert startup.review_state == "draft"


@pytest.mark.asyncio
async def test_feed_investor_resume_keeps_org_and_role_after_fsm_clear(feed_env) -> None:
    """Save draft → clear FSM → Continue resumes next step with org/role intact."""
    from raundo.wizard_keyboards import BTN_CONTINUE, BTN_SAVE

    feed = feed_env["feed"]
    recording: RecordingSession = feed_env["recording"]
    session_factory = feed_env["session_factory"]
    await _accept_consent(feed_env)

    mid = 600
    await feed(Update(update_id=mid, message=_message(text="/investor", message_id=mid)))
    mid += 1
    await feed(
        Update(update_id=mid, message=_message(text=BTN_CREATE_INVESTOR, message_id=mid))
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Resume User", message_id=mid)))
    mid += 1
    prompt = _message(text="type", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=f"{CB_ITYPE}vc_fund", message=prompt, callback_id="r1"),
        )
    )
    mid += 1
    prompt = _message(text="sm", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(
                data=f"{CB_SMOC}direct_equity", message=prompt, callback_id="r2"
            ),
        )
    )
    mid += 1
    prompt = _message(text="smd", message_id=mid)
    await feed(
        Update(
            update_id=mid,
            callback_query=_callback(data=CB_DONE_SM, message=prompt, callback_id="r3"),
        )
    )
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Resume Capital", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="Partner", message_id=mid)))
    # Save draft and clear FSM
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_SAVE, message_id=mid)))

    async with session_factory() as session:
        profile = (await session.execute(select(InvestorProfile))).scalar_one()
        assert profile.organization_name == "Resume Capital"
        assert profile.position == "Partner"
        mem = (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == profile.user_id
                )
            )
        ).scalar_one()
        assert mem.role == "Partner"

    recording.calls.clear()
    mid += 1
    await feed(Update(update_id=mid, message=_message(text="/investor", message_id=mid)))
    mid += 1
    await feed(Update(update_id=mid, message=_message(text=BTN_CONTINUE, message_id=mid)))
    texts = sent_texts(recording)
    # Resume should ask for strategy (org+position already filled), not re-ask org name
    assert any("стратег" in t.lower() or "мандат" in t.lower() for t in texts)
    assert not any("название фонда" in t.lower() for t in texts)

