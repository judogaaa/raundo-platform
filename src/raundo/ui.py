"""Single source of truth for safe Telegram text formatting.

Rules:
- The bot sends HTML by default (see main.py), so every dynamic value MUST go
  through ``esc`` before interpolation.
- Never interpolate raw user input into a message without escaping.
- Statuses, money, dates and limits have exactly one human-readable formatter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape as _html_escape

MAX_MESSAGE = 4000
MAX_CAPTION = 1000

REVIEW_STATUS_LABELS = {
    "draft": "Черновик",
    "submitted": "На проверке",
    "under_review": "На проверке",
    "approved": "Одобрено",
    "changes_requested": "Требуются изменения",
    "pending_changes": "Требуются изменения",
    "rejected": "Отклонено",
    "archived": "В архиве",
    "suspended": "Приостановлено",
    "claimed": "Ожидает проверки",
    "revoked": "Отозвано",
}

MONETIZATION_LABELS = {
    "not_yet": "Пока не монетизируется",
    "planned": "Модель запланирована",
    "active": "Уже зарабатывает",
}

TICKET_STATUS_LABELS = {
    "new": "Новое",
    "in_progress": "В работе",
    "waiting_user": "Ожидает уточнения",
    "awaiting_confirmation": "Ожидает подтверждения",
    "needs_help": "Требуется помощь",
    "resolved": "Решено",
    "closed": "Закрыто",
}

REQUEST_STATUS_LABELS = {
    "pending": "ждёт ответа",
    "accepted": "принят",
    "declined": "отклонён",
}


def esc(value: object | None, default: str = "Не указано") -> str:
    """Escape a dynamic value for HTML parse mode; never returns 'None'."""
    if value is None:
        return _html_escape(default)
    text = str(value).strip()
    if not text:
        return _html_escape(default)
    return _html_escape(text, quote=False)


def b(value: object | None, default: str = "Не указано") -> str:
    return f"<b>{esc(value, default)}</b>"


def i(value: object | None, default: str = "Не указано") -> str:
    return f"<i>{esc(value, default)}</i>"


def code(value: object | None) -> str:
    return f"<code>{esc(value)}</code>"


def link(url: object | None, label: object | None = None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return esc(None)
    if not raw.startswith(("https://", "http://")):
        return esc(raw)
    return f'<a href="{_html_escape(raw, quote=True)}">{esc(label or raw)}</a>'


def status(value: object | None) -> str:
    """Human moderation status. The only review-state label map in the bot."""
    return REVIEW_STATUS_LABELS.get(str(value or ""), "Не указан")


def ticket_status(value: object | None) -> str:
    return TICKET_STATUS_LABELS.get(str(value or ""), "Обрабатывается")


def request_status(value: object | None) -> str:
    return REQUEST_STATUS_LABELS.get(str(value or ""), "обработан")


def plan_title(value: object | None) -> str:
    """Premium plan code → human title; never shows organization_pro."""
    from raundo.premium_catalog import PLAN_TITLES

    code = str(value or "")
    return PLAN_TITLES.get(code, "Premium")


def monetization(value: object | None) -> str:
    return MONETIZATION_LABELS.get(str(value or ""), "Не указано")


def money(amount_minor: int | None, currency: str | None) -> str:
    from raundo.domain import format_ask_amount

    return format_ask_amount(amount_minor, currency) or "Не указано"


def money_range(
    lo: int | None, hi: int | None, currency: str | None, *, discuss: bool = False
) -> str:
    from raundo.domain import format_cheque_range

    return format_cheque_range(lo, hi, currency, discuss=discuss) or "Не указан"


def date(value: datetime | None) -> str:
    if value is None:
        return "Не указана"
    moment = value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.strftime("%d.%m.%Y")


def limit_line(label: str, used: int, limit: int, plan: str | None = None) -> str:
    base = f"{esc(label)}: {int(used)} из {int(limit)}"
    if plan:
        return f"{base} · {esc(plan)}"
    return f"{base} · бесплатный тариф"


def bullet(value: object | None, default: str = "Не указано") -> str:
    return f"• {esc(value, default)}"


def section(title: str, lines: list[str]) -> str:
    body = "\n".join(line for line in lines if line)
    return f"<b>{esc(title)}</b>\n{body}" if body else f"<b>{esc(title)}</b>"


def truncate(text: str, limit: int = MAX_MESSAGE) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def caption(text: str) -> str:
    return truncate(text, MAX_CAPTION)


def empty_block(title: str, hint: str) -> str:
    return f"<b>{esc(title)}</b>\n<i>{esc(hint)}</i>"
