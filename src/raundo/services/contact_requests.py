"""Contact requests between viewers and founders with mutual-consent contact reveal."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import REVIEW_APPROVED
from raundo.models.contact import (
    REQUEST_ACCEPTED,
    REQUEST_DECLINED,
    REQUEST_PENDING,
    ContactRequest,
)
from raundo.models.startup import Startup
from raundo.models.user import User, UserProfile
from raundo.services.notifications import NotificationService

MAX_REQUESTS_PER_DAY = 5
MAX_MESSAGE_LEN = 600


class ContactError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


async def _profile(session: AsyncSession, user_id: uuid.UUID) -> UserProfile:
    profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


async def _notify(
    session: AsyncSession,
    *,
    kind: str,
    recipient_id: uuid.UUID,
    request: ContactRequest,
    name: str,
    text: str,
    dedupe: str,
) -> None:
    await NotificationService(session).enqueue(
        kind=kind,
        dedupe_key=dedupe,
        recipient_user_id=recipient_id,
        resource_type="contact_request",
        resource_id=str(request.id),
        safe_payload={"name": name, "text": text},
    )


async def create_request(
    session: AsyncSession,
    *,
    requester_id: uuid.UUID,
    startup_id: uuid.UUID,
    message: str | None,
) -> ContactRequest:
    startup = await session.get(Startup, startup_id)
    if (
        startup is None
        or startup.deleted_at is not None
        or startup.review_state != REVIEW_APPROVED
        or startup.published_at is None
    ):
        raise ContactError("Проект больше недоступен.", code="hidden")
    if startup.owner_user_id == requester_id:
        raise ContactError("Нельзя отправить запрос самому себе.", code="self_request")
    requester = await session.get(User, requester_id)
    if requester is None or requester.status != "active":
        raise ContactError("Ваш аккаунт не может отправлять запросы.", code="inactive")

    text = (message or "").strip()
    if len(text) > MAX_MESSAGE_LEN:
        raise ContactError("Сообщение слишком длинное.", code="too_long")

    existing = await session.scalar(
        select(ContactRequest).where(
            ContactRequest.startup_id == startup_id,
            ContactRequest.requester_user_id == requester_id,
        )
    )
    if existing is not None:
        if existing.status == REQUEST_PENDING:
            raise ContactError("Запрос уже отправлен и ждёт ответа.", code="pending")
        if existing.status == REQUEST_ACCEPTED:
            raise ContactError("Запрос уже принят.", code="accepted")
        raise ContactError(
            "Основатель отклонил запрос. Повторно отправить его нельзя.", code="declined"
        )

    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    recent = await session.scalar(
        select(func.count())
        .select_from(ContactRequest)
        .where(
            ContactRequest.requester_user_id == requester_id,
            ContactRequest.created_at >= day_ago,
        )
    )
    if int(recent or 0) >= MAX_REQUESTS_PER_DAY:
        raise ContactError(
            "Слишком много запросов за сутки. Попробуйте позже.", code="rate_limited"
        )

    request = ContactRequest(
        id=uuid.uuid4(),
        startup_id=startup_id,
        requester_user_id=requester_id,
        owner_user_id=startup.owner_user_id,
        message=text or None,
        status=REQUEST_PENDING,
    )
    session.add(request)
    await session.flush()

    who = " ".join(
        part for part in (requester.first_name, requester.last_name) if part
    ) or "Пользователь RAUNDO"
    if requester.username:
        who = f"{who} (@{requester.username})"
    await _notify(
        session,
        kind="contact.requested",
        recipient_id=startup.owner_user_id,
        request=request,
        name=startup.name or "Проект",
        text=(
            f"Новый запрос на знакомство по проекту «{startup.name or ''}».\n\n"
            f"От: {who}\n"
            + (f"Сообщение: {text}" if text else "")
            + "\n\nОтветить можно в разделе «🤝 Запросы»."
        ),
        dedupe=f"contact_requested:{request.id}",
    )
    return request


async def decide(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    request_id: uuid.UUID,
    accept: bool,
) -> ContactRequest:
    request = await session.get(ContactRequest, request_id)
    if request is None:
        raise ContactError("Запрос не найден.", code="missing")
    if request.owner_user_id != owner_id:
        raise ContactError("Это чужой запрос.", code="forbidden")
    if request.status != REQUEST_PENDING:
        raise ContactError("Запрос уже обработан.", code="decided")

    startup = await session.get(Startup, request.startup_id)
    if startup is None or startup.deleted_at is not None:
        raise ContactError("Проект больше недоступен.", code="hidden")

    request.status = REQUEST_ACCEPTED if accept else REQUEST_DECLINED
    request.decided_at = datetime.now(timezone.utc)
    await session.flush()

    if accept:
        owner_reveal = bool(startup.show_contact_after_accept) and bool(startup.contact_username)
        owner_contact = f"@{startup.contact_username}" if owner_reveal else ""
        requester_profile = await _profile(session, request.requester_user_id)
        requester_reveal = (
            requester_profile.contact_mode == "username"
            and bool(requester_profile.contact_username)
        )
        requester_contact = (
            f"@{requester_profile.contact_username}" if requester_reveal else ""
        )
        text = (
            f"Основатель принял ваш запрос по проекту «{startup.name or ''}»."
            + (f"\nКонтакт: {owner_contact}" if owner_contact else
               "\nКонтакт не раскрыт: сообщение можно передать через RAUNDO.")
        )
        await _notify(
            session,
            kind="contact.accepted",
            recipient_id=request.requester_user_id,
            request=request,
            name=startup.name or "Проект",
            text=text,
            dedupe=f"contact_accepted:{request.id}",
        )
        owner_text = (
            f"Вы приняли запрос по проекту «{startup.name or ''}»."
            + (f"\nКонтакт заявителя: {requester_contact}" if requester_contact else
               "\nКонтакт заявителя не раскрыт: общение через RAUNDO.")
        )
        await _notify(
            session,
            kind="contact.accepted",
            recipient_id=owner_id,
            request=request,
            name=startup.name or "Проект",
            text=owner_text,
            dedupe=f"contact_accepted_owner:{request.id}",
        )
    else:
        await _notify(
            session,
            kind="contact.declined",
            recipient_id=request.requester_user_id,
            request=request,
            name=startup.name or "Проект",
            text=f"Основатель отклонил запрос по проекту «{startup.name or ''}».",
            dedupe=f"contact_declined:{request.id}",
        )
    return request


async def requests_for_owner(
    session: AsyncSession, owner_id: uuid.UUID, *, limit: int = 20
) -> list[tuple[ContactRequest, Startup, User | None]]:
    rows = await session.execute(
        select(ContactRequest, Startup, User)
        .join(Startup, Startup.id == ContactRequest.startup_id)
        .outerjoin(User, User.id == ContactRequest.requester_user_id)
        .where(ContactRequest.owner_user_id == owner_id)
        .order_by(ContactRequest.created_at.desc())
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in rows.all()]


async def requests_for_requester(
    session: AsyncSession, requester_id: uuid.UUID, *, limit: int = 20
) -> list[tuple[ContactRequest, Startup]]:
    rows = await session.execute(
        select(ContactRequest, Startup)
        .join(Startup, Startup.id == ContactRequest.startup_id)
        .where(ContactRequest.requester_user_id == requester_id)
        .order_by(ContactRequest.created_at.desc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in rows.all()]
