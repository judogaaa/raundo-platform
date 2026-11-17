"""Support ticket lifecycle shared by the Telegram bot and the web console.

The bot creates tickets and user replies, the admin API answers them. Staff replies are
delivered through the outbox worker, so every staff message can report a real delivery
state instead of a decorative label.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.admin_panel import AdminAuditLog, SupportMessage, SupportTicket
from raundo.models.system import OutboxEvent
from raundo.services.notifications import NotificationService

CATEGORIES: dict[str, str] = {
    "account": "Аккаунт и доступ",
    "startup": "Анкета стартапа",
    "investor": "Анкета инвестора",
    "premium": "Premium",
    "other": "Другое",
}

TICKET_STATUSES: tuple[str, ...] = (
    "new",
    "in_progress",
    "waiting_user",
    "awaiting_confirmation",
    "needs_help",
    "resolved",
    "closed",
)
ACTIVE_TICKET_STATUSES = frozenset(
    {"new", "in_progress", "waiting_user", "awaiting_confirmation", "needs_help"}
)
# Statuses where the user is expected to act in Telegram.
USER_ACTION_STATUSES = frozenset({"waiting_user", "awaiting_confirmation", "needs_help"})
REOPENABLE_STATUSES = frozenset({"resolved", "closed"})
# A staff answer waits for the user's confirmation; a clarification waits for
# the user's information. They are different states on purpose.
# Manual transitions only. `awaiting_confirmation` and `needs_help` are set by
# the reply/confirmation flows, never by a status button: otherwise a ticket
# could wait for buttons that were never sent.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"in_progress", "waiting_user", "resolved", "closed"}),
    "in_progress": frozenset({"new", "waiting_user", "resolved", "closed"}),
    "waiting_user": frozenset({"new", "in_progress", "resolved", "closed"}),
    "awaiting_confirmation": frozenset({"new", "in_progress", "waiting_user", "resolved", "closed"}),
    "needs_help": frozenset({"new", "in_progress", "waiting_user", "resolved", "closed"}),
    "resolved": frozenset({"new", "in_progress", "waiting_user", "closed"}),
    "closed": frozenset({"new", "in_progress"}),
}
REPLY_OUTCOMES = frozenset({"answer", "clarify"})
# Backward-compatible aliases for callers that still send the old names.
_LEGACY_OUTCOMES = {"resolve": "answer", "waiting_user": "clarify"}
ATTACHMENT_KINDS = frozenset({"photo", "document"})
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
NUMBER_PREFIX = "SUP-"
_NUMBER_RETRIES = 5


class SupportError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Attachment:
    kind: str
    file_id: str
    name: str | None = None
    size: int | None = None

    @classmethod
    def from_telegram(cls, message) -> Attachment | None:  # noqa: ANN001 - aiogram Message
        if message.document is not None:
            document = message.document
            return cls(
                kind="document",
                file_id=document.file_id,
                name=(document.file_name or "document")[:255],
                size=document.file_size,
            )
        if message.photo:
            photo = message.photo[-1]
            return cls(kind="photo", file_id=photo.file_id, name=None, size=photo.file_size)
        return None

    def validate(self) -> None:
        if self.kind not in ATTACHMENT_KINDS:
            raise SupportError("Недопустимый тип вложения.", code="attachment_kind")
        if self.size is not None and self.size > MAX_ATTACHMENT_BYTES:
            raise SupportError("Файл слишком большой. Максимум 5 МБ.", code="attachment_size")


def _format_body(text: str, attachment: Attachment | None) -> str:
    """Store the user's text only; attachment metadata lives in its own columns."""
    del attachment  # accepted for API compatibility; no system suffix in body
    body = (text or "").strip()
    return body[:4000]


async def _next_ticket_number(session: AsyncSession) -> str:
    numbers = (
        await session.execute(select(SupportTicket.public_number))
    ).scalars()
    highest = 0
    for number in numbers:
        suffix = (number or "").removeprefix(NUMBER_PREFIX)
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{NUMBER_PREFIX}{highest + 1:06d}"


async def create_ticket(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject: str,
    body: str,
    category: str | None = None,
    attachment: Attachment | None = None,
) -> SupportTicket:
    clean_subject = (subject or "").strip()
    if len(clean_subject) < 3:
        raise SupportError("Тема слишком короткая.", code="subject_short")
    if category is not None and category not in CATEGORIES:
        raise SupportError("Неизвестная категория.", code="category")
    if attachment is not None:
        attachment.validate()
    text = _format_body(body, attachment)
    if len(text) < 3:
        raise SupportError("Сообщение слишком короткое.", code="body_short")
    now = datetime.now(timezone.utc)
    for _attempt in range(_NUMBER_RETRIES):
        ticket = SupportTicket(
            id=uuid.uuid4(),
            public_number=await _next_ticket_number(session),
            user_id=user_id,
            subject=clean_subject[:160],
            category=category,
            status="new",
            priority="normal",
            updated_at=now,
        )
        try:
            async with session.begin_nested():
                session.add(ticket)
                await session.flush()
                session.add(
                    SupportMessage(
                        id=uuid.uuid4(),
                        ticket_id=ticket.id,
                        author_kind="user",
                        body=text,
                        attachment_kind=attachment.kind if attachment else None,
                        attachment_name=attachment.name if attachment else None,
                        attachment_size=attachment.size if attachment else None,
                        attachment_file_id=attachment.file_id if attachment else None,
                    )
                )
                await session.flush()
        except IntegrityError:
            continue
        return ticket
    raise SupportError("Не удалось создать обращение. Повторите позже.", code="number_conflict")


def _ticket_guard(ticket: SupportTicket, user_id: uuid.UUID) -> None:
    if ticket.user_id != user_id:
        raise SupportError("Обращение недоступно.", code="forbidden")


async def add_user_message(
    session: AsyncSession,
    *,
    ticket: SupportTicket,
    user_id: uuid.UUID,
    body: str,
    attachment: Attachment | None = None,
) -> SupportMessage:
    _ticket_guard(ticket, user_id)
    if ticket.status == "closed":
        raise SupportError("Обращение закрыто. Создайте новое.", code="closed")
    if attachment is not None:
        attachment.validate()
    text = _format_body(body, attachment)
    if not text:
        raise SupportError("Введите текст сообщения.", code="body_empty")
    message = SupportMessage(
        id=uuid.uuid4(),
        ticket_id=ticket.id,
        author_kind="user",
        body=text,
        attachment_kind=attachment.kind if attachment else None,
        attachment_name=attachment.name if attachment else None,
        attachment_size=attachment.size if attachment else None,
        attachment_file_id=attachment.file_id if attachment else None,
    )
    session.add(message)
    # A user message always reopens the conversation: the ticket returns to the
    # staff queue and all completion/confirmation facts stop applying.
    ticket.status = "new"
    ticket.resolved_at = None
    ticket.resolved_by_id = None
    ticket.confirmed_at = None
    ticket.confirmed_by_user_id = None
    ticket.disputed_at = None
    ticket.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return message


async def staff_reply(
    session: AsyncSession,
    *,
    ticket: SupportTicket,
    staff_id: uuid.UUID,
    body: str,
    internal: bool = False,
    outcome: str = "answer",
) -> tuple[SupportMessage, OutboxEvent | None]:
    """Store a staff message and move the ticket according to the explicit outcome.

    ``outcome="answer"`` sends the answer and waits for the user's confirmation:
    the ticket becomes "awaiting_confirmation" and the Telegram message carries
    the yes/no buttons for this round. ``outcome="clarify"`` is only for a real
    request for more details ("waiting_user", no buttons).
    Internal notes never change the status or the confirmation facts.
    """
    outcome = _LEGACY_OUTCOMES.get(outcome, outcome)
    if outcome not in REPLY_OUTCOMES:
        raise SupportError("Неизвестный итог ответа.", code="outcome")
    text = (body or "").strip()
    if not text:
        raise SupportError("Введите текст ответа.", code="body_empty")
    if ticket.status == "closed" and not internal:
        raise SupportError(
            "Обращение закрыто. Переоткройте его перед ответом.", code="closed"
        )
    kind = "note" if internal else "staff"
    now = datetime.now(timezone.utc)
    # Double-submit guard: an identical staff message inside this window is the
    # same action, not a second answer. Returns the stored message and its event.
    recent = await session.scalar(
        select(SupportMessage)
        .where(
            SupportMessage.ticket_id == ticket.id,
            SupportMessage.author_kind == kind,
            SupportMessage.body == text[:4000],
            SupportMessage.created_at >= now - timedelta(seconds=10),
        )
        .order_by(SupportMessage.created_at.desc())
        .limit(1)
    )
    if recent is not None:
        event = (
            await session.get(OutboxEvent, recent.outbox_event_id)
            if recent.outbox_event_id
            else None
        )
        return recent, event
    message = SupportMessage(
        id=uuid.uuid4(),
        ticket_id=ticket.id,
        author_kind=kind,
        staff_id=staff_id,
        body=text[:4000],
    )
    event: OutboxEvent | None = None
    if not internal:
        # Every answer opens a new confirmation round; buttons from older rounds
        # are rejected by the callback handler.
        ticket.confirmation_round = int(ticket.confirmation_round or 0) + 1
        payload: dict[str, object] = {
            "text": message.body,
            "number": ticket.public_number,
        }
        if outcome == "answer":
            payload["confirm_round"] = ticket.confirmation_round
        event = await NotificationService(session).enqueue_event(
            kind="support.reply",
            dedupe_key=f"support_reply:{message.id}",
            recipient_user_id=ticket.user_id,
            resource_type="ticket",
            resource_id=str(ticket.id),
            safe_payload=payload,
        )
        message.outbox_event_id = event.id
        ticket.status = "awaiting_confirmation" if outcome == "answer" else "waiting_user"
        ticket.resolved_at = None
        ticket.resolved_by_id = None
        ticket.confirmed_at = None
        ticket.confirmed_by_user_id = None
        ticket.disputed_at = None
    else:
        if ticket.status == "new":
            ticket.status = "in_progress"
    ticket.updated_at = now
    session.add(message)
    await session.flush()
    return message, event


def confirmation_buttons(ticket: SupportTicket) -> tuple[tuple[str, str], ...]:
    """(label, callback_data) pairs for the current confirmation round."""
    token = f"{ticket.id}:{int(ticket.confirmation_round or 0)}"
    return (
        ("✅ Да, решён", f"sv:yes:{token}"),
        ("❌ Нет", f"sv:no:{token}"),
    )


def confirm_resolution(
    ticket: SupportTicket, *, user_id: uuid.UUID, session: AsyncSession | None = None
) -> bool:
    """The user confirms the answer. Returns False for an idempotent repeat."""
    _ticket_guard(ticket, user_id)
    if ticket.status == "resolved" and ticket.confirmed_by_user_id == user_id:
        return False
    if ticket.status != "awaiting_confirmation":
        raise SupportError("Подтверждение уже не требуется.", code="not_awaiting")
    now = datetime.now(timezone.utc)
    ticket.status = "resolved"
    ticket.resolved_at = now
    ticket.resolved_by_id = None
    ticket.confirmed_at = now
    ticket.confirmed_by_user_id = user_id
    ticket.disputed_at = None
    ticket.updated_at = now
    if session is not None:
        session.add(
            AdminAuditLog(
                id=uuid.uuid4(),
                actor_id=None,
                action="ticket_user_confirmed",
            resource_type="ticket",
            resource_id=str(ticket.id),
                before_state={"status": "awaiting_confirmation"},
                after_state={"status": "resolved", "confirmed_by": str(user_id)},
                reason=None,
            )
        )
    return True


def reject_resolution(
    ticket: SupportTicket, *, user_id: uuid.UUID, session: AsyncSession | None = None
) -> bool:
    """The user says the issue is not solved. Returns False for a repeat."""
    _ticket_guard(ticket, user_id)
    if ticket.status == "needs_help" and ticket.disputed_at is not None:
        return False
    if ticket.status not in {"awaiting_confirmation", "needs_help"}:
        raise SupportError("Обращение уже не ждёт подтверждения.", code="not_awaiting")
    now = datetime.now(timezone.utc)
    ticket.status = "needs_help"
    ticket.resolved_at = None
    ticket.resolved_by_id = None
    ticket.confirmed_at = None
    ticket.confirmed_by_user_id = None
    ticket.disputed_at = now
    ticket.updated_at = now
    if session is not None:
        session.add(
            AdminAuditLog(
                id=uuid.uuid4(),
                actor_id=None,
                action="ticket_user_disputed",
                resource_type="ticket",
                resource_id=str(ticket.id),
                before_state={"status": "awaiting_confirmation"},
                after_state={"status": "needs_help"},
                reason=None,
            )
        )
    return True


def add_system_message(
    session: AsyncSession, *, ticket: SupportTicket, body: str
) -> SupportMessage:
    """History entry for a user's yes/no; not delivered to Telegram."""
    message = SupportMessage(
        id=uuid.uuid4(),
        ticket_id=ticket.id,
        author_kind="system",
        body=body[:4000],
    )
    session.add(message)
    return message


def set_ticket_status(
    ticket: SupportTicket,
    status: str,
    *,
    actor_id: uuid.UUID,
    reason: str | None = None,
) -> None:
    """Apply an explicit manual transition with the same facts the reply writes."""
    del reason  # the audit log stores the reason; the row stores the fact
    if status not in TICKET_STATUSES:
        raise SupportError("Недопустимый статус.", code="status")
    if status == ticket.status:
        raise SupportError("Обращение уже в этом статусе.", code="same_status")
    allowed = ALLOWED_TRANSITIONS.get(ticket.status, frozenset())
    if status not in allowed:
        raise SupportError("Недопустимый переход статуса.", code="bad_transition")
    now = datetime.now(timezone.utc)
    ticket.status = status
    if status in {"resolved", "closed"}:
        ticket.resolved_at = now
        ticket.resolved_by_id = actor_id
        # A manual decision is not a user confirmation.
        ticket.confirmed_at = None
        ticket.confirmed_by_user_id = None
        ticket.disputed_at = None
    else:
        # Reopened: the previous completion must not be shown as current.
        ticket.resolved_at = None
        ticket.resolved_by_id = None
        ticket.confirmed_at = None
        ticket.confirmed_by_user_id = None
        ticket.disputed_at = None
    ticket.updated_at = now


def delivery_state(message: SupportMessage, event: OutboxEvent | None) -> str:
    if message.author_kind != "staff":
        return "not_applicable"
    if event is None:
        return "unknown"
    return {
        "pending": "pending",
        "processing": "processing",
        "retry_scheduled": "retry_scheduled",
        "accepted": "delivered",
        "failed": "failed",
        "undeliverable": "failed",
    }.get(event.state, event.state)
