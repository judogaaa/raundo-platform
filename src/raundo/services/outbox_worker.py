"""Deliver pending outbox events without a second Telegram polling loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.admin_panel import OutboxDeliveryMeta
from raundo.models.system import OutboxEvent
from raundo.models.user import User

MAX_ATTEMPTS = 8

# Kinds that respect the per-user "notifications" setting in /settings.
# Critical notices (moderation, support, complaints, restrictions, contact
# requests) are always delivered; only digests/matches can be opted out.
OPTIONAL_NOTIFICATION_KINDS = frozenset({"community.digest", "feed.match"})


class TelegramRateLimitError(Exception):
    def __init__(self, retry_after: int = 30) -> None:
        self.retry_after = retry_after
        super().__init__("rate_limited")


class TelegramBlockedError(Exception):
    pass


class TelegramSender(Protocol):
    async def send_text(self, telegram_id: int, text: str) -> None: ...


CONFIRMATION_QUESTION = "Удалось решить ваш вопрос?"
MESSAGE_LIMIT = 3800


def support_buttons(event: OutboxEvent) -> tuple[tuple[str, str], ...] | None:
    """Yes/No buttons for a support answer of a specific confirmation round.

    Only this outbox event gets buttons; every other notification stays plain
    text. Callback data is bound to the ticket id and the round token.
    """
    if event.kind != "support.reply":
        return None
    payload = event.safe_payload or {}
    round_token = payload.get("confirm_round")
    resource_id = event.resource_id
    if round_token is None or not resource_id:
        return None
    token = f"{resource_id}:{int(round_token)}"
    return (
        ("✅ Да, решён", f"sv:yes:{token}"),
        ("❌ Нет", f"sv:no:{token}"),
    )


def _split_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split long text on line boundaries without breaking Telegram limits."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            parts.append(current.rstrip("\n"))
            current = ""
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current += line
    if current.strip():
        parts.append(current.rstrip("\n"))
    return parts or [text[:limit]]


def _payload_text(payload: dict, key: str) -> str:
    value = str(payload.get(key) or "").strip()
    return value or "не указано"


def render_message(event: OutboxEvent) -> str:
    payload = event.safe_payload or {}
    name = payload.get("name") or "Анкета"
    if event.kind.startswith("organization.") or event.kind.startswith("membership."):
        if event.kind.endswith("approved"):
            if event.kind == "membership.approved":
                mandate = "с правом прямых инвестиций" if payload.get("mandate") else "без права прямых инвестиций"
                return (
                    "Представительство подтверждено\n\n"
                    f"Ваши полномочия в «{name}» подтверждены администрацией RAUNDO ({mandate})."
                )
            return f"Организация подтверждена\n\n«{name}» прошла проверку RAUNDO."
        if event.kind.endswith("revoked"):
            return f"Полномочия отозваны\n\nПричина:\n{_payload_text(payload, 'reason')}"
        if event.kind.endswith("rejected"):
            return f"Заявка отклонена\n\nПричина:\n{_payload_text(payload, 'reason')}"
        if event.kind.endswith("changes_requested"):
            return f"Нужны уточнения\n\nЗамечания:\n{_payload_text(payload, 'comment')}"
        return payload.get("text") or "Уведомление RAUNDO"
    if event.kind.endswith("approved"):
        return f"Анкета одобрена\n\nВаш проект «{name}» успешно прошёл проверку RAUNDO."
    if event.kind.endswith("changes_requested"):
        return (
            "Необходимо уточнить анкету\n\n"
            "Администрация RAUNDO рассмотрела вашу заявку.\n"
            f"Замечания:\n{_payload_text(payload, 'comment')}"
        )
    if event.kind.endswith("rejected"):
        return f"Заявка отклонена\n\nПричина:\n{_payload_text(payload, 'reason')}"
    if event.kind == "support.reply":
        return f"Ответ поддержки RAUNDO\n\n{_payload_text(payload, 'text')}"
    if event.kind == "user.restricted":
        return (
            "Доступ к RAUNDO ограничен\n\n"
            f"Причина:\n{_payload_text(payload, 'reason')}\n\n"
            "Если это ошибка, напишите в поддержку: /support"
        )
    if event.kind == "user.unrestricted":
        return "Доступ к RAUNDO восстановлен\n\nОграничение снято администрацией."
    if event.kind == "complaint.resolved":
        return (
            f"Жалоба {payload.get('number') or ''} рассмотрена\n\n"
            f"Решение:\n{_payload_text(payload, 'comment')}"
        )
    if event.kind == "payment.refunded":
        order_number = _payload_text(payload, "order_number")
        return (
            "Платёж возвращён\n\n"
            f"Заказ {order_number}: возврат выполнен.\n"
            "Доступ Premium скорректирован по условиям возврата. Вопросы: /support."
        )
    if event.kind == "receipt.issued":
        order_number = _payload_text(payload, "order_number")
        receipt_url = _payload_text(payload, "receipt_url")
        return (
            "Чек по оплате\n\n"
            f"Заказ {order_number}.\n"
            f"Ссылка на чек: {receipt_url}"
        )
    if event.kind == "premium.purchased":
        return (
            "Подписка активирована\n\n"
            f"{_payload_text(payload, 'plan')} · {_payload_text(payload, 'period')}\n"
            f"Действует до {_payload_text(payload, 'until')}."
        )
    if event.kind == "complaint.dismissed":
        return (
            f"Жалоба {payload.get('number') or ''} закрыта\n\n"
            f"Комментарий:\n{_payload_text(payload, 'comment')}"
        )
    return payload.get("text") or "Уведомление RAUNDO"


async def _notifications_enabled(session: AsyncSession, user_id) -> bool:
    from raundo.models.billing import CommunityNotificationPref

    pref = await session.scalar(
        select(CommunityNotificationPref).where(
            CommunityNotificationPref.user_id == user_id
        )
    )
    return True if pref is None else bool(pref.enabled)


async def deliver_pending(session: AsyncSession, sender: TelegramSender, *, limit: int = 20) -> int:
    now = datetime.now(timezone.utc)
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.state.in_(("pending", "retry_scheduled")),
            OutboxEvent.next_attempt_at <= now,
        )
        .order_by(OutboxEvent.created_at)
        .limit(limit)
    )
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    rows = await session.execute(stmt)
    sent = 0
    for event in rows.scalars().all():
        meta = await session.get(OutboxDeliveryMeta, event.id)
        if meta is None:
            meta = OutboxDeliveryMeta(outbox_id=event.id, attempt_count=0)
            session.add(meta)
        user = None
        if event.recipient_user_id is not None:
            user = await session.get(User, event.recipient_user_id)
        if user is None:
            event.state = "failed"
            meta.last_error = "no_recipient"
            continue
        if event.kind in OPTIONAL_NOTIFICATION_KINDS and not await _notifications_enabled(
            session, user.id
        ):
            event.state = "not_applicable"
            meta.last_error = "notifications_disabled"
            continue
        event.state = "processing"
        await session.flush()
        buttons = support_buttons(event)
        text = render_message(event)
        try:
            if buttons:
                # The question belongs to the same message as the buttons.
                full_text = f"{text}\n\n{CONFIRMATION_QUESTION}"
                parts = _split_text(full_text)
                send_with_buttons = getattr(sender, "send_with_buttons", None)
                if send_with_buttons is None:
                    # Test transports may only implement send_text.
                    for part in parts:
                        await sender.send_text(user.telegram_id, part)
                else:
                    for part in parts[:-1]:
                        await sender.send_text(user.telegram_id, part)
                    await send_with_buttons(user.telegram_id, parts[-1], buttons)
            else:
                for part in _split_text(text):
                    await sender.send_text(user.telegram_id, part)
        except TelegramRateLimitError as exc:
            meta.attempt_count += 1
            meta.last_error = "telegram_429"
            delay = max(exc.retry_after, min(3600, 2 ** min(meta.attempt_count, 8)))
            event.next_attempt_at = now + timedelta(seconds=delay)
            event.state = "retry_scheduled"
            continue
        except TelegramBlockedError:
            event.state = "failed"
            meta.last_error = "bot_blocked"
            continue
        except Exception as exc:  # noqa: BLE001
            meta.attempt_count += 1
            meta.last_error = type(exc).__name__
            delay = min(3600, 2 ** min(meta.attempt_count, 8))
            event.next_attempt_at = now + timedelta(seconds=delay)
            if meta.attempt_count >= MAX_ATTEMPTS:
                event.state = "failed"
            else:
                event.state = "retry_scheduled"
            continue
        event.state = "accepted"
        meta.delivered_at = now
        meta.last_error = None
        sent += 1
    await session.flush()
    return sent
