"""Users and complaints: directory, restrictions, complaint workflow and bot intake."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser
from sqlalchemy import select

from raundo.middlewares.restriction import NOTICE, RestrictionMiddleware
from raundo.models.admin_panel import (
    AdminAuditLog,
    Complaint,
    SupportTicket,
)
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.routers.complaints import (
    ComplaintFlow,
    on_evidence,
    on_reason,
    on_type,
    open_complaint,
)
from raundo.services.admin_directory import (
    DirectoryError,
    list_users,
    restrict_user,
    unrestrict_user,
    user_detail,
)
from raundo.services.complaints import (
    ComplaintError,
    Evidence,
    apply_status,
    complaint_detail,
    create_complaint,
    list_complaints,
    set_complaint_status,
)

ACTOR = uuid.uuid4()


async def _user(session, telegram_id: int, username: str, **kwargs) -> User:  # noqa: ANN001
    user = User(
        id=uuid.uuid4(),
        telegram_id=telegram_id,
        username=username,
        first_name=kwargs.pop("first_name", "Тест"),
        last_name=kwargs.pop("last_name", "Пользователь"),
        status=kwargs.pop("status", "active"),
        **kwargs,
    )
    session.add(user)
    await session.flush()
    return user


def _message(text: str, user_id: int = 700001) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.document = None
    msg.photo = None
    return msg


async def _state() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))


# --- directory -------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_telegram_id_username_and_name(session) -> None:  # noqa: ANN001
    first = await _user(session, 910001, "findme_by_username", first_name="Алиса")
    second = await _user(session, 910002, "other_login", first_name="Борис")

    by_id = await list_users(session, q="910001")
    assert [item["id"] for item in by_id["items"]] == [str(first.id)]

    by_username = await list_users(session, q="findme_by")
    assert [item["id"] for item in by_username["items"]] == [str(first.id)]

    by_name = await list_users(session, q="Борис")
    assert [item["id"] for item in by_name["items"]] == [str(second.id)]

    paginated = await list_users(session, page=1, page_size=1, sort="created_at")
    assert paginated["page_size"] == 1
    assert paginated["pages"] == max(1, paginated["pages"])
    assert len(paginated["items"]) == 1


@pytest.mark.asyncio
async def test_search_unknown_profile_type_is_rejected(session) -> None:  # noqa: ANN001
    with pytest.raises(DirectoryError) as exc:
        await list_users(session, profile_type="alien")
    assert exc.value.code == "profile_type"


@pytest.mark.asyncio
async def test_user_detail_is_scoped_and_has_no_raw_secrets(session) -> None:  # noqa: ANN001
    user = await _user(session, 910010, "detail_user")
    detail = await user_detail(session, user.id)
    assert detail["telegram_id"] == 910010
    assert detail["startups"] == []
    assert detail["investor"] is None
    assert detail["restrictions"] == []
    assert detail["admin_actions"] == []
    assert "password_hash" not in detail
    with pytest.raises(DirectoryError) as exc:
        await user_detail(session, uuid.uuid4())
    assert exc.value.code == "missing"


@pytest.mark.asyncio
async def test_restrict_and_unrestrict_are_idempotent_and_audited(session) -> None:  # noqa: ANN001
    user = await _user(session, 910020, "restrict_me")
    restriction = await restrict_user(
        session, user_id=user.id, actor_id=ACTOR, reason="Спам в обращениях"
    )
    await session.refresh(user)
    assert user.status == "restricted"
    assert restriction.active is True

    with pytest.raises(DirectoryError) as exc:
        await restrict_user(
            session, user_id=user.id, actor_id=ACTOR, reason="Повторное ограничение"
        )
    assert exc.value.code == "already_restricted"

    audit = list(
        (
            await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.resource_id == str(user.id))
            )
        ).scalars()
    )
    assert [row.action for row in audit] == ["restrict"]
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.kind == "user.restricted")
    )
    assert event is not None and event.recipient_user_id == user.id

    released = await unrestrict_user(
        session, user_id=user.id, actor_id=ACTOR, reason="Разобрались, ошибка"
    )
    assert released == 1
    await session.refresh(user)
    assert user.status == "active"

    with pytest.raises(DirectoryError) as exc:
        await unrestrict_user(
            session, user_id=user.id, actor_id=ACTOR, reason="Уже разблокирован"
        )
    assert exc.value.code == "not_restricted"


@pytest.mark.asyncio
async def test_restriction_requires_meaningful_reason(session) -> None:  # noqa: ANN001
    user = await _user(session, 910030, "short_reason")
    with pytest.raises(DirectoryError) as exc:
        await restrict_user(session, user_id=user.id, actor_id=ACTOR, reason="ой")
    assert exc.value.code == "reason_required"


# --- complaints ------------------------------------------------------------


@pytest.mark.asyncio
async def test_complaint_intake_validation_and_dedupe(session) -> None:  # noqa: ANN001
    reporter = await _user(session, 910100, "reporter")
    with pytest.raises(ComplaintError) as exc:
        await create_complaint(
            session,
            reporter_user_id=reporter.id,
            target_type="other",
            target_id="",
            reason="мало",
        )
    assert exc.value.code == "reason_short"

    complaint = await create_complaint(
        session,
        reporter_user_id=reporter.id,
        target_type="other",
        target_id="",
        reason="Описание проблемы длиной больше десяти символов",
    )
    assert complaint.public_number.startswith("CMP-")
    assert complaint.status == "open"
    assert complaint.priority == "normal"

    with pytest.raises(ComplaintError) as exc:
        await create_complaint(
            session,
            reporter_user_id=reporter.id,
            target_type="other",
            target_id="",
            reason="Повторная жалоба на тот же объект",
        )
    assert exc.value.code == "duplicate"


@pytest.mark.asyncio
async def test_complaint_transitions_and_resolution_comment(session) -> None:  # noqa: ANN001
    reporter = await _user(session, 910110, "reporter2")
    complaint = await create_complaint(
        session,
        reporter_user_id=reporter.id,
        target_type="other",
        target_id="",
        reason="Нарушение правил, подробное описание",
    )

    with pytest.raises(ComplaintError) as exc:
        set_complaint_status(complaint, "resolved", comment=None, actor_id=ACTOR)
    assert exc.value.code == "comment_required"

    resolved = await apply_status(
        session, complaint=complaint, status="resolved", comment="Решение принято", actor_id=ACTOR
    )
    assert resolved["status"] == "resolved"
    assert complaint.resolution_comment == "Решение принято"
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.kind == "complaint.resolved")
    )
    assert event is not None

    with pytest.raises(ComplaintError) as exc:
        await apply_status(
            session,
            complaint=complaint,
            status="resolved",
            comment="Повторное решение",
            actor_id=ACTOR,
        )
    assert exc.value.code == "no_change"

    reopened = await apply_status(
        session, complaint=complaint, status="in_review", comment=None, actor_id=ACTOR
    )
    assert reopened["status"] == "in_review"


@pytest.mark.asyncio
async def test_complaint_detail_and_filters(session) -> None:  # noqa: ANN001
    reporter = await _user(session, 910120, "reporter3")
    target = await _user(session, 910121, "target_user", first_name="Цель")
    complaint = await create_complaint(
        session,
        reporter_user_id=reporter.id,
        target_type="user",
        target_id=str(target.id),
        reason="Оскорбления в личных сообщениях <script>alert(1)</script>",
        priority="high",
        evidence=Evidence(kind="photo", file_id="secret-file-id", size=1024),
    )

    listing = await list_complaints(session, status="open", target_type="user")
    assert listing["total"] >= 1
    item = next(i for i in listing["items"] if i["id"] == str(complaint.id))
    assert item["target_title"] == "Цель Пользователь"
    assert item["has_evidence"] is True

    detail = await complaint_detail(session, complaint.id)
    assert detail["reporter"]["telegram_id"] == 910120
    assert detail["target"]["section"] == "users"
    assert detail["evidence"] == {"kind": "photo", "name": None, "size": 1024}
    assert detail["allowed_statuses"] == sorted({"in_review", "resolved", "dismissed"})
    # XSS stays data: the API never rewrites it into markup.
    assert "<script>" in detail["reason"]
    assert "secret-file-id" not in str(detail)
    assert complaint.attachment_file_id == "secret-file-id"


@pytest.mark.asyncio
async def test_resolving_user_complaint_restricts_target(session) -> None:  # noqa: ANN001
    from raundo.services.admin_directory import _active_restriction

    reporter = await _user(session, 910130, "reporter4")
    target = await _user(session, 910131, "abusive")
    complaint = await create_complaint(
        session,
        reporter_user_id=reporter.id,
        target_type="user",
        target_id=str(target.id),
        reason="Угрозы в переписке, прошу проверить",
    )
    result = await apply_status(
        session,
        complaint=complaint,
        status="resolved",
        comment="Подтверждено, доступ ограничен",
        actor_id=ACTOR,
        restrict_target=True,
    )
    assert result["restricted"] is True
    await session.refresh(target)
    assert target.status == "restricted"
    assert await _active_restriction(session, target.id) is not None


@pytest.mark.asyncio
async def test_complaint_listing_rejects_fake_status(session) -> None:  # noqa: ANN001
    with pytest.raises(ComplaintError) as exc:
        await list_complaints(session, status="archived")
    assert exc.value.code == "status"


# --- bot intake ------------------------------------------------------------


@pytest.mark.asyncio
async def test_complaint_bot_flow_other_target(session) -> None:  # noqa: ANN001
    db_user = await _user(session, 700001, "bot_reporter")
    state = await _state()
    await open_complaint(_message("/complaint"), state)
    await on_type(_message("4"), state, db_user)
    await on_reason(_message("Коротко"), state)
    await on_reason(_message("Подробное описание проблемы без объекта"), state)
    await on_evidence(_message("Пропустить"), state, session, db_user)

    row = await session.execute(select(Complaint).where(Complaint.reporter_user_id == db_user.id))
    complaint = row.scalar_one()
    assert complaint.target_type == "other"
    assert complaint.status == "open"


@pytest.mark.asyncio
async def test_complaint_bot_flow_search_without_matches(session) -> None:  # noqa: ANN001
    db_user = await _user(session, 700002, "bot_reporter2")
    state = await _state()
    await open_complaint(_message("/complaint"), state)
    await on_type(_message("1"), state, db_user)
    assert await state.get_state() == ComplaintFlow.target_search.state
    message = _message("Несуществующий стартап")
    from raundo.routers.complaints import on_search

    await on_search(message, state, session, db_user)
    assert "Ничего не найдено" in message.answer.await_args.args[0]


# --- restriction middleware ------------------------------------------------


def _tg_message(text: str) -> Message:
    return Message.model_construct(
        message_id=1,
        date=0,
        chat=Chat.model_construct(id=1, type="private"),
        from_user=TgUser.model_construct(id=700001, is_bot=False, first_name="U"),
        text=text,
    )


@pytest.mark.asyncio
async def test_restriction_middleware_blocks_and_keeps_support_open(monkeypatch) -> None:  # noqa: ANN001
    restricted = User(id=uuid.uuid4(), telegram_id=1, status="restricted")
    session = MagicMock()
    session.scalar = AsyncMock(return_value=uuid.uuid4())
    middleware = RestrictionMiddleware()
    handler = AsyncMock(return_value="handled")

    monkeypatch.setattr(
        Message, "answer", AsyncMock(), raising=False
    )

    blocked = await middleware(handler, _tg_message("привет"), {"db_user": restricted, "session": session})
    assert blocked is None
    handler.assert_not_awaited()
    Message.answer.assert_awaited_once_with(NOTICE)

    Message.answer.reset_mock()
    handler.reset_mock()
    allowed = await middleware(
        handler, _tg_message("/support"), {"db_user": restricted, "session": session}
    )
    assert allowed == "handled"
    handler.assert_awaited_once()

    # No active restriction row: a stale flag must not lock the user out.
    session.scalar = AsyncMock(return_value=None)
    handler.reset_mock()
    passed = await middleware(handler, _tg_message("привет"), {"db_user": restricted, "session": session})
    assert passed == "handled"


@pytest.mark.asyncio
async def test_restriction_middleware_ignores_active_users(monkeypatch) -> None:  # noqa: ANN001
    active = User(id=uuid.uuid4(), telegram_id=2, status="active")
    handler = AsyncMock(return_value="ok")
    middleware = RestrictionMiddleware()
    result = await middleware(handler, _tg_message("привет"), {"db_user": active, "session": MagicMock()})
    assert result == "ok"


def test_support_ticket_model_untouched() -> None:
    """Regression guard: complaint work must not alter support schema."""
    assert "category" in SupportTicket.__table__.columns
