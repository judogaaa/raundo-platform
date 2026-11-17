"""Outbox worker integration on the suite DB: contact events, moderation, prefs."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.billing import CommunityNotificationPref
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.outbox_worker import deliver_pending, render_message
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 992_950


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


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


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
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.recipient_user_id.in_(old))
            )
            await session.execute(
                delete(CommunityNotificationPref).where(
                    CommunityNotificationPref.user_id.in_(old)
                )
            )
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _user(factory, offset: int):  # noqa: ANN001, ANN202
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + offset,
            username=f"outbox_user_{offset}",
            first_name="Outbox",
            last_name=str(offset),
            language_code="ru",
        )
        await session.commit()
        return user


async def _event(factory, user, kind: str, *, text: str, recipient=True) -> uuid.UUID:  # noqa: ANN001
    async with factory() as session:
        event = OutboxEvent(
            id=uuid.uuid4(),
            kind=kind,
            dedupe_key=f"{kind}:{uuid.uuid4().hex}",
            recipient_user_id=user.id if recipient else None,
            safe_payload={"name": "Проект", "text": text},
            state="pending",
        )
        from datetime import datetime, timezone

        event.next_attempt_at = datetime.now(timezone.utc)
        session.add(event)
        await session.commit()
        return event.id


async def _state(factory, event_id: uuid.UUID):  # noqa: ANN001, ANN202
    async with factory() as session:
        return await session.get(OutboxEvent, event_id)


async def test_contact_events_are_delivered(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 1)
    texts = {
        "contact.requested": "Новый запрос на знакомство",
        "contact.accepted": "Основатель принял ваш запрос",
        "contact.declined": "Основатель отклонил запрос",
    }
    ids = {}
    for kind, text in texts.items():
        ids[kind] = await _event(factory, user, kind, text=text)
    sender = FakeSender()
    async with factory() as session:
        await deliver_pending(session, sender, limit=10)
        await session.commit()
    # Other suite tests may have left their own pending events; only ours matter.
    assert len(sender.sent) >= 3
    joined = " ".join(text for _, text in sender.sent)
    for text in texts.values():
        assert text in joined
    for kind, event_id in ids.items():
        event = await _state(factory, event_id)
        assert event.state == "accepted", kind


async def test_moderation_and_support_rendering(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 2)
    cases = {
        "startup.approved": "Анкета одобрена",
        "startup.changes_requested": "Необходимо уточнить анкету",
        "startup.rejected": "Заявка отклонена",
        "support.reply": "Ответ поддержки",
        "user.restricted": "Доступ к RAUNDO ограничен",
    }
    ids = {}
    for kind in cases:
        ids[kind] = await _event(
            factory, user, kind, text="Текст", recipient=True
        )
    sender = FakeSender()
    async with factory() as session:
        await deliver_pending(session, sender, limit=10)
        await session.commit()
    joined = " ".join(text for _, text in sender.sent)
    for marker in cases.values():
        assert marker in joined, marker


def test_render_message_uses_payload_text_for_contacts() -> None:
    from types import SimpleNamespace

    event = SimpleNamespace(
        kind="contact.requested",
        safe_payload={"text": "Текст запроса", "name": "Проект"},
    )
    assert render_message(event) == "Текст запроса"


async def test_optional_kind_respects_disabled_notifications(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 3)
    async with factory() as session:
        session.add(
            CommunityNotificationPref(
                id=uuid.uuid4(), user_id=user.id, enabled=False
            )
        )
        await session.commit()
    await _event(factory, user, "community.digest", text="Дайджест")
    sender = FakeSender()
    async with factory() as session:
        sent = await deliver_pending(session, sender, limit=10)
        await session.commit()
    assert sent == 0
    assert sender.sent == []


async def test_optional_kind_delivers_when_enabled(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 4)
    async with factory() as session:
        session.add(
            CommunityNotificationPref(
                id=uuid.uuid4(), user_id=user.id, enabled=True
            )
        )
        await session.commit()
    await _event(factory, user, "community.digest", text="Дайджест")
    sender = FakeSender()
    async with factory() as session:
        await deliver_pending(session, sender, limit=10)
        await session.commit()
    assert len(sender.sent) == 1
