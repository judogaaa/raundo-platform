"""Complaint intake (Telegram) and resolution (web admin).

The bot creates complaints; the admin decides. Every transition is audited, the reporter
gets a real Telegram notification through the outbox, and an optional restriction on a
user target goes through the same audited path as the user directory.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.models.admin_panel import AdminAccount, AdminAuditLog, Complaint, UserRestriction
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization
from raundo.models.startup import Startup
from raundo.models.user import User
from raundo.services.admin_directory import DirectoryError, restrict_user
from raundo.services.notifications import NotificationService

TARGET_TYPES = {
    "startup": "Стартап",
    "investor": "Инвестор",
    "user": "Пользователь",
    "organization": "Организация",
    "other": "Другое",
}
OPEN_STATUSES = frozenset({"open", "in_review"})
STATUSES = ("open", "in_review", "resolved", "dismissed")
PRIORITIES = ("low", "normal", "high")
NUMBER_PREFIX = "CMP-"
MAX_REASON = 4000
MIN_REASON = 10
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
_NUMBER_RETRIES = 5

# Allowed transitions. Resolved/dismissed can be reopened for a second look.
TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_review", "resolved", "dismissed"},
    "in_review": {"resolved", "dismissed", "open"},
    "resolved": {"in_review"},
    "dismissed": {"in_review"},
}


class ComplaintError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Evidence:
    kind: str
    file_id: str
    name: str | None = None
    size: int | None = None

    @classmethod
    def from_telegram(cls, message) -> Evidence | None:  # noqa: ANN001 - aiogram Message
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
        if self.kind not in {"photo", "document"}:
            raise ComplaintError("Недопустимый тип вложения.", code="attachment_kind")
        if self.size is not None and self.size > MAX_ATTACHMENT_BYTES:
            raise ComplaintError("Файл слишком большой. Максимум 5 МБ.", code="attachment_size")


def _next_number(existing: list[str]) -> str:
    highest = 0
    for number in existing:
        suffix = (number or "").removeprefix(NUMBER_PREFIX)
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{NUMBER_PREFIX}{highest + 1:06d}"


async def _allocate_number(session: AsyncSession) -> str:
    numbers = list(
        (await session.execute(select(Complaint.public_number))).scalars()
    )
    return _next_number(numbers)


async def create_complaint(
    session: AsyncSession,
    *,
    reporter_user_id: uuid.UUID,
    target_type: str,
    target_id: str,
    reason: str,
    priority: str = "normal",
    evidence: Evidence | None = None,
) -> Complaint:
    if target_type not in TARGET_TYPES:
        raise ComplaintError("Неизвестный объект жалобы.", code="target_type")
    if priority not in PRIORITIES:
        raise ComplaintError("Неизвестный приоритет.", code="priority")
    text = (reason or "").strip()
    if len(text) < MIN_REASON:
        raise ComplaintError(
            f"Опишите проблему не короче {MIN_REASON} символов.", code="reason_short"
        )
    if evidence is not None:
        evidence.validate()
    target = (target_id or "").strip()
    if target_type != "other" and not target:
        raise ComplaintError("Не указан объект жалобы.", code="target_missing")

    duplicate = await session.scalar(
        select(Complaint.id).where(
            Complaint.reporter_user_id == reporter_user_id,
            Complaint.target_type == target_type,
            Complaint.target_id == target,
            Complaint.status.in_(OPEN_STATUSES),
        )
    )
    if duplicate is not None:
        raise ComplaintError(
            "У вас уже есть открытая жалоба на этот объект.", code="duplicate"
        )

    for _attempt in range(_NUMBER_RETRIES):
        complaint = Complaint(
            id=uuid.uuid4(),
            public_number=await _allocate_number(session),
            reporter_user_id=reporter_user_id,
            target_type=target_type,
            target_id=target,
            reason=text[:MAX_REASON],
            priority=priority,
            status="open",
            attachment_kind=evidence.kind if evidence else None,
            attachment_name=evidence.name if evidence else None,
            attachment_size=evidence.size if evidence else None,
            attachment_file_id=evidence.file_id if evidence else None,
        )
        try:
            async with session.begin_nested():
                session.add(complaint)
                await session.flush()
        except IntegrityError:
            continue
        return complaint
    raise ComplaintError(
        "Не удалось создать жалобу. Повторите позже.", code="number_conflict"
    )


def set_complaint_status(
    complaint: Complaint,
    status: str,
    *,
    comment: str | None,
    actor_id: uuid.UUID,
) -> None:
    if status not in STATUSES:
        raise ComplaintError("Недопустимый статус.", code="status")
    if complaint.status == status:
        raise ComplaintError("Жалоба уже в этом статусе.", code="no_change")
    allowed = TRANSITIONS.get(complaint.status, set())
    if status not in allowed:
        raise ComplaintError(
            "Недопустимый переход статуса жалобы.", code="transition"
        )
    text = (comment or "").strip()
    if status in {"resolved", "dismissed"} and len(text) < 8:
        raise ComplaintError(
            "Добавьте решение не короче 8 символов.", code="comment_required"
        )
    complaint.status = status
    if status in {"resolved", "dismissed"}:
        complaint.resolution_comment = text[:2000]
        complaint.resolved_by = actor_id
        complaint.resolved_at = datetime.now(timezone.utc)


def _audit(session: AsyncSession, **kwargs) -> None:  # noqa: ANN003
    session.add(AdminAuditLog(id=uuid.uuid4(), **kwargs))


async def apply_status(
    session: AsyncSession,
    *,
    complaint: Complaint,
    status: str,
    comment: str | None,
    actor_id: uuid.UUID,
    restrict_target: bool = False,
) -> dict:
    before = complaint.status
    restriction_created = False
    if restrict_target and complaint.target_type == "user" and status == "resolved":
        try:
            await restrict_user(
                session,
                user_id=uuid.UUID(complaint.target_id),
                actor_id=actor_id,
                reason=(comment or complaint.reason)[:2000],
            )
            restriction_created = True
        except DirectoryError as exc:
            if exc.code != "already_restricted":
                raise ComplaintError(
                    "Ограничение не применено: проверьте объект жалобы.",
                    code="restrict_failed",
                ) from exc
        except ValueError as exc:
            raise ComplaintError(
                "Ограничение не применено: некорректный пользователь.",
                code="restrict_failed",
            ) from exc
    set_complaint_status(complaint, status, comment=comment, actor_id=actor_id)

    _audit(
        session,
        actor_id=actor_id,
        action="complaint_status",
        resource_type="complaint",
        resource_id=str(complaint.id),
        before_state={"status": before},
        after_state={"status": complaint.status, "restricted": restriction_created},
        reason=(comment or "").strip() or None,
    )
    if status in {"resolved", "dismissed"}:
        kind = "complaint.resolved" if status == "resolved" else "complaint.dismissed"
        await NotificationService(session).enqueue(
            kind=kind,
            dedupe_key=f"complaint:{complaint.id}:{status}",
            recipient_user_id=complaint.reporter_user_id,
            resource_type="complaint",
            resource_id=str(complaint.id),
            safe_payload={
                "number": complaint.public_number,
                "comment": complaint.resolution_comment or "",
                "status": status,
            },
        )
    await session.flush()
    return {
        "status": complaint.status,
        "restricted": restriction_created,
        "resolution_comment": complaint.resolution_comment,
    }


async def target_summary(
    session: AsyncSession, *, target_type: str, target_id: str
) -> dict | None:
    """Resolve a complaint target without leaking more than the console already shows."""
    if target_type == "user":
        try:
            user = await session.get(User, uuid.UUID(target_id))
        except (ValueError, TypeError):
            return None
        if user is None:
            return None
        return {
            "type": "user",
            "id": str(user.id),
            "title": " ".join(part for part in (user.first_name, user.last_name) if part)
            or user.username
            or str(user.telegram_id),
            "subtitle": f"@{user.username}" if user.username else f"ID {user.telegram_id}",
            "section": "users",
            "status": user.status,
        }
    if target_type == "startup":
        try:
            startup = await session.get(Startup, uuid.UUID(target_id))
        except (ValueError, TypeError):
            return None
        if startup is None:
            return None
        return {
            "type": "startup",
            "id": str(startup.id),
            "title": startup.name,
            "subtitle": f"Стартап · {ui.status(startup.review_state)}",
            "section": "moderation",
            "status": startup.review_state,
        }
    if target_type == "investor":
        try:
            investor = await session.get(InvestorProfile, uuid.UUID(target_id))
        except (ValueError, TypeError):
            return None
        if investor is None:
            return None
        return {
            "type": "investor",
            "id": str(investor.id),
            "title": investor.display_name or "Инвестор",
            "subtitle": f"Инвестор · {ui.status(investor.review_status)}",
            "section": "moderation",
            "status": investor.review_status,
        }
    if target_type == "organization":
        try:
            org = await session.get(FundingOrganization, uuid.UUID(target_id))
        except (ValueError, TypeError):
            return None
        if org is None:
            return None
        return {
            "type": "organization",
            "id": str(org.id),
            "title": org.name,
            "subtitle": f"Организация · {ui.status(org.review_status)}",
            "section": "orgs",
            "status": org.review_status,
        }
    return None


async def list_complaints(
    session: AsyncSession,
    *,
    status: str | None = None,
    target_type: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    conditions = []
    if status:
        if status not in STATUSES:
            raise ComplaintError("Недопустимый статус.", code="status")
        conditions.append(Complaint.status == status)
    if target_type:
        if target_type not in TARGET_TYPES:
            raise ComplaintError("Неизвестный объект жалобы.", code="target_type")
        conditions.append(Complaint.target_type == target_type)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        conditions.append(
            or_(Complaint.public_number.ilike(like), Complaint.reason.ilike(like))
        )
    total = await session.scalar(
        select(func.count()).select_from(Complaint).where(*conditions)
    )
    rows = (
        await session.execute(
            select(Complaint)
            .where(*conditions)
            .order_by(Complaint.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars()
    items = []
    for complaint in rows:
        target = await target_summary(
            session, target_type=complaint.target_type, target_id=complaint.target_id
        )
        items.append(
            {
                "id": str(complaint.id),
                "number": complaint.public_number,
                "target_type": complaint.target_type,
                "target_id": complaint.target_id,
                "target_title": target["title"] if target else None,
                "reason": complaint.reason,
                "status": complaint.status,
                "priority": complaint.priority,
                "created_at": complaint.created_at.isoformat()
                if complaint.created_at
                else None,
                "has_evidence": bool(complaint.attachment_kind),
            }
        )
    return {
        "items": items,
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "pages": max(1, (int(total or 0) + page_size - 1) // page_size),
        "statuses": list(STATUSES),
        "target_types": TARGET_TYPES,
    }


async def complaint_detail(session: AsyncSession, complaint_id: uuid.UUID) -> dict:
    complaint = await session.get(Complaint, complaint_id)
    if complaint is None:
        raise ComplaintError("Жалоба не найдена.", code="missing")
    reporter = await session.get(User, complaint.reporter_user_id)
    target = await target_summary(
        session, target_type=complaint.target_type, target_id=complaint.target_id
    )
    history_rows = (
        await session.execute(
            select(AdminAuditLog, AdminAccount)
            .outerjoin(AdminAccount, AdminAccount.id == AdminAuditLog.actor_id)
            .where(
                AdminAuditLog.resource_type == "complaint",
                AdminAuditLog.resource_id == str(complaint.id),
            )
            .order_by(AdminAuditLog.created_at.asc())
        )
    ).all()
    resolver = (
        await session.get(AdminAccount, complaint.resolved_by)
        if complaint.resolved_by
        else None
    )
    restrictions = (
        await session.execute(
            select(UserRestriction)
            .where(UserRestriction.user_id == complaint.reporter_user_id)
            .order_by(UserRestriction.created_at.desc())
            .limit(5)
        )
    ).scalars()
    return {
        "id": str(complaint.id),
        "number": complaint.public_number,
        "target_type": complaint.target_type,
        "target_type_label": TARGET_TYPES.get(complaint.target_type, complaint.target_type),
        "target_id": complaint.target_id,
        "target": target,
        "reason": complaint.reason,
        "status": complaint.status,
        "priority": complaint.priority,
        "created_at": complaint.created_at.isoformat() if complaint.created_at else None,
        "resolution_comment": complaint.resolution_comment,
        "resolved_at": complaint.resolved_at.isoformat() if complaint.resolved_at else None,
        "resolution_author": resolver.login if resolver else None,
        "reporter": {
            "id": str(reporter.id) if reporter else None,
            "telegram_id": reporter.telegram_id if reporter else None,
            "username": reporter.username if reporter else None,
            "name": " ".join(
                part for part in (reporter.first_name, reporter.last_name) if part
            )
            if reporter
            else None,
            "status": reporter.status if reporter else None,
            "restrictions": [
                {
                    "id": str(r.id),
                    "reason": r.reason,
                    "active": bool(r.active),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in restrictions
            ],
        },
        "evidence": {
            "kind": complaint.attachment_kind,
            "name": complaint.attachment_name,
            "size": complaint.attachment_size,
        }
        if complaint.attachment_kind
        else None,
        "history": [
            {
                "action": log.action,
                "actor": actor.login if actor else None,
                "reason": log.reason,
                "before_state": log.before_state,
                "after_state": log.after_state,
                "created_at": log.created_at.isoformat(),
            }
            for log, actor in history_rows
        ],
        "allowed_statuses": sorted(TRANSITIONS.get(complaint.status, set())),
        "can_restrict_target": complaint.target_type == "user",
    }


__all__ = [
    "ComplaintError",
    "Evidence",
    "OPEN_STATUSES",
    "PRIORITIES",
    "STATUSES",
    "TARGET_TYPES",
    "TRANSITIONS",
    "apply_status",
    "complaint_detail",
    "create_complaint",
    "list_complaints",
    "set_complaint_status",
    "target_summary",
]
