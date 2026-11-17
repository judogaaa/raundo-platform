"""Pure product invariants, independent from Telegram transport and DB."""

from __future__ import annotations

import re
from collections.abc import Collection
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, Overflow
from urllib.parse import urlparse

from raundo.participants import (
    ALLOWED_CURRENCIES,
    CHEQUE_RELEVANT_TYPES,
    LEGACY_PARTICIPANT_TYPE_MAP,
    ORG_NAME_REQUIRED_TYPES,
    PARTICIPANT_TYPE_LABELS,
    SUPPORT_MODE_LABELS,
    canonical_participant_type,
    cheque_is_relevant,
    needs_organization_name,
    participant_type_label,
)

MIN_STARTUP_PHOTOS = 1
MAX_STARTUP_PHOTOS = 3

NAME_MIN, NAME_MAX = 2, 80
# New pitches: one finished sentence. Column remains String(240) for legacy drafts.
PITCH_MIN, PITCH_MAX = 50, 120
LEGACY_PITCH_MAX = 240
DESC_MIN, DESC_MAX = 100, 500
STRATEGY_MIN, STRATEGY_MAX = 100, 1200
PITCH_PROMPT = (
    "Одним предложением опишите, что делает ваш продукт, для кого он и какую "
    "пользу приносит. От 50 до 120 символов."
)

REVIEW_DRAFT = "draft"
REVIEW_SUBMITTED = "submitted"
REVIEW_UNDER_REVIEW = "under_review"
REVIEW_CHANGES = "changes_requested"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_SUSPENDED = "suspended"
REVIEW_ARCHIVED = "archived"
REVIEW_PENDING_CHANGES = "pending_changes"
REVIEW_SUPERSEDED = "superseded"

EDITABLE_STATES = frozenset(
    {
        REVIEW_DRAFT,
        REVIEW_CHANGES,
        REVIEW_REJECTED,
        REVIEW_SUBMITTED,
        REVIEW_UNDER_REVIEW,
        REVIEW_APPROVED,
        REVIEW_PENDING_CHANGES,
        REVIEW_SUSPENDED,
    }
)
SUBMITTABLE_STATES = frozenset(
    {
        REVIEW_DRAFT,
        REVIEW_CHANGES,
        REVIEW_REJECTED,
        REVIEW_PENDING_CHANGES,
    }
)

# Legacy Issue #2 codes kept for compatibility; UI prefers PARTICIPANT_TYPE_LABELS.
INVESTOR_TYPES = tuple(PARTICIPANT_TYPE_LABELS.keys())
INVESTOR_TYPE_LABELS: dict[str, str] = dict(PARTICIPANT_TYPE_LABELS)

# Also accept legacy codes in callbacks/DB
KNOWN_PARTICIPANT_TYPES = frozenset(PARTICIPANT_TYPE_LABELS) | frozenset(
    LEGACY_PARTICIPANT_TYPE_MAP
)

STARTUP_INTENTS = (
    "investment",
    "users",
    "pilots",
    "partners",
    "team",
    "feedback",
)

STARTUP_INTENT_LABELS: dict[str, str] = {
    "investment": "Инвестиции",
    "users": "Пользователи",
    "pilots": "Пилоты",
    "partners": "Партнёры",
    "team": "Команда",
    "feedback": "Обратная связь",
}

# Founder-declared traction only — RAUNDO does not independently audit these.
STARTUP_PROGRESS_LABELS: dict[str, str] = {
    "none_yet": "Ранняя стадия / идея",
    "prototype": "Прототип",
    "mvp": "MVP / рабочий продукт",
    "pilots": "Пилоты",
    "users": "Есть пользователи",
    "revenue": "Есть выручка",
}

STARTUP_AUDIENCE_LABELS: dict[str, str] = {
    "b2c": "B2C",
    "b2b_smb": "B2B SMB",
    "b2b_enterprise": "B2B Enterprise",
    "b2g": "B2G",
    "developers": "Разработчики",
    "other": "Другое",
}

# Distinguish planned vs active earning — never imply revenue from a plan alone.
STARTUP_MONETIZATION_LABELS: dict[str, str] = {
    "not_yet": "Пока не монетизируем",
    "planned": "Модель спланирована, выручки нет",
    "active": "Уже зарабатываем",
}

# Public labels for contact_policy codes stored in DB (codes stay relay|username).
CONTACT_POLICY_LABELS: dict[str, str] = {
    "relay": "Через RAUNDO после подтверждения запроса",
    "username": "По взаимному согласию после подтверждения запроса",
}

STARTUP_FUNDRAISING_LABELS: dict[str, str] = {
    "not_raising": "Сейчас не привлекаем раунд",
    "pre_seed": "Pre-seed",
    "seed": "Seed",
    "series_a": "Series A+",
    "bridge": "Bridge",
}

STARTUP_USE_OF_FUNDS_LABELS: dict[str, str] = {
    "product": "Продукт",
    "growth": "Рост и маркетинг",
    "team": "Команда",
    "sales": "Продажи",
    "ops": "Операции",
    "other": "Другое",
}

NOTE_MAX = 240

ORG_REQUIRED_TYPES = ORG_NAME_REQUIRED_TYPES

CURRENCY_DECIMALS: dict[str, int] = {
    "RUB": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CNY": 2,
    "KZT": 2,
    "USDT": 2,
}

MAX_MONEY_MINOR = (2**63) - 1
MIN_MONEY_MINOR = 0

_HTTPS_RE = re.compile(r"^https://", re.IGNORECASE)
_HOSTNAME_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)


def validate_startup_photo_count(count: int) -> bool:
    """Only a startup with 1–3 actual usable images may be submitted."""
    return MIN_STARTUP_PHOTOS <= count <= MAX_STARTUP_PHOTOS


# Issue #5 (D014): FREE accounts keep exactly one photo per new startup card.
FREE_PHOTO_CAP = 1
PRO_PHOTO_CAP = 3


def effective_photo_cap(*, grandfathered_gallery: bool, has_active_startup_pro: bool) -> int:
    """
    Server-side photo cap for a startup.

    Legacy startups created before the cap (flag set by migration) keep the
    full 3-photo gallery; active Startup PRO restores it for new startups.
    Never inferred from the current photo count.
    """
    if grandfathered_gallery or has_active_startup_pro:
        return PRO_PHOTO_CAP
    return FREE_PHOTO_CAP


BOOST_WINDOW_MINUTES = 30
BOOST_COOLDOWN_HOURS = 24
MAX_ORG_SEATS = 5


def boost_end_time(
    *, started_at: datetime, subscription_expires_at: datetime | None
) -> datetime:
    """A boost never outlives the subscription that paid for it."""
    hard_end = started_at + timedelta(minutes=BOOST_WINDOW_MINUTES)
    if subscription_expires_at is not None and subscription_expires_at < hard_end:
        return subscription_expires_at
    return hard_end


def boost_cooldown_over(now: datetime, last_boost_started_at: datetime | None) -> bool:
    """One boost per startup per rolling 24 hours; unused boosts do not stack."""
    if last_boost_started_at is None:
        return True
    return now - last_boost_started_at >= timedelta(hours=BOOST_COOLDOWN_HOURS)


def validate_investor_preferences(
    chosen_sectors: Collection[str],
    chosen_stages: Collection[str],
) -> bool:
    """Investor forms require at least one sector and stage."""
    return bool(chosen_sectors) and bool(chosen_stages)


def is_listed_project(approval_state: str) -> bool:
    """A submitted or hidden startup is not in public discovery."""
    return approval_state == "published"


def can_receive_verified_investor_label(review_state: str) -> bool:
    """A paid subscription alone can never issue the verified label."""
    return review_state == REVIEW_APPROVED


def can_share_private_contact(
    *,
    request_state: str,
    founder_consented: bool,
    requester_consented: bool,
) -> bool:
    """A simple like or payment is never sufficient to disclose contact."""
    return request_state == "accepted" and founder_consented and requester_consented


def can_show_in_investor_feed(
    *,
    startup_sectors: Collection[str],
    startup_stage: str,
    preferred_sectors: Collection[str],
    preferred_stages: Collection[str],
    show_all: bool = False,
) -> bool:
    """Strict preferences by default; only explicit show_all overrides them."""
    if show_all:
        return True
    if not preferred_sectors or not preferred_stages:
        return False
    return bool(set(startup_sectors) & set(preferred_sectors)) and (
        startup_stage in preferred_stages
    )


def validate_name(value: str) -> str | None:
    text = value.strip()
    if not (NAME_MIN <= len(text) <= NAME_MAX):
        return f"Название: от {NAME_MIN} до {NAME_MAX} символов."
    return None


def validate_pitch(value: str, *, previous: str | None = None) -> str | None:
    """
    New pitches must be PITCH_MIN..PITCH_MAX.

    Legacy drafts may already be outside that range (up to LEGACY_PITCH_MAX):
    unchanged text is accepted so continue/edit does not wipe data; submit
    readiness still requires the new limit.
    """
    text = value.strip()
    prev = (previous or "").strip()
    if prev and text == prev and len(prev) <= LEGACY_PITCH_MAX:
        # Keep any previously stored draft pitch (including old 20–240 range).
        if len(prev) >= 1:
            return None
    if not (PITCH_MIN <= len(text) <= PITCH_MAX):
        return (
            f"Короткий питч: одно предложение от {PITCH_MIN} до {PITCH_MAX} символов "
            f"(сейчас {len(text)})."
        )
    return None


def validate_description(value: str, *, previous: str | None = None) -> str | None:
    """
    New input must be DESC_MIN..DESC_MAX.

    Legacy drafts may already exceed DESC_MAX: unchanged text is accepted so data
    is not truncated/wiped; readiness/submit still asks the founder to shorten.
    """
    text = value.strip()
    prev = (previous or "").strip()
    if prev and text == prev and len(prev) > DESC_MAX:
        if len(text) >= DESC_MIN:
            return None
    if not (DESC_MIN <= len(text) <= DESC_MAX):
        return f"Описание: от {DESC_MIN} до {DESC_MAX} символов."
    return None


def validate_strategy(value: str) -> str | None:
    text = value.strip()
    if not (STRATEGY_MIN <= len(text) <= STRATEGY_MAX):
        return f"Стратегия: от {STRATEGY_MIN} до {STRATEGY_MAX} символов."
    return None


def normalize_https_url(value: str) -> str | None:
    """Accept https URLs with a real hostname; reject credentials and http."""
    text = value.strip()
    if not text or len(text) > 512:
        return None
    if any(ch.isspace() for ch in text):
        return None
    if not _HTTPS_RE.match(text):
        if text.startswith("http://"):
            return None
        text = "https://" + text.lstrip("/")
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname
    if not host or "@" in (parsed.netloc or ""):
        return None
    # Reject raw IPv6 brackets / empty; require dotted hostname with TLD
    if not _HOSTNAME_RE.match(host):
        return None
    # Rebuild without credentials / fragments noise
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://{host.lower()}{path}{query}"


def _finite_decimal(value: int | Decimal | str) -> Decimal | None:
    """Parse a Decimal that is finite (rejects NaN/Infinity). Never raises."""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def major_units_to_minor(major: int | Decimal, currency: str) -> int | None:
    """Convert human major units (e.g. 15000000 RUB) to integer minor units."""
    code = currency.upper().strip()
    if code not in ALLOWED_CURRENCIES:
        return None
    decimals = CURRENCY_DECIMALS.get(code, 2)
    amount = _finite_decimal(major)
    if amount is None or amount < 0:
        return None
    scale = Decimal(10) ** decimals
    try:
        scaled = amount * scale
        if not scaled.is_finite():
            return None
        minor = int(scaled)
    except (OverflowError, Overflow, InvalidOperation, ValueError):
        return None
    if minor < MIN_MONEY_MINOR or minor > MAX_MONEY_MINOR:
        return None
    return minor


_CURRENCY_DISPLAY: dict[str, str] = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CHF": "CHF",
    "CNY": "¥",
    "KZT": "₸",
}


def apply_investment_ask_step(
    text: str,
    *,
    current_minor: int | None,
    current_currency: str | None,
    clear_label: str,
    skip_label: str,
) -> tuple[int | None, str | None] | None:
    """
    Resolve ask amount after an investment_ask wizard input.

    Returns (minor, currency) on success, or None if the text is not a valid
    amount / recognized action. Skip preserves previous values; clear removes.
    """
    raw = text.strip()
    if raw == clear_label:
        return None, None
    if raw == skip_label:
        return current_minor, current_currency
    parsed = parse_single_amount_major(raw)
    if parsed is None:
        return None
    return parsed


def format_ask_amount(minor: int | None, currency: str | None) -> str | None:
    """Human ask for cards: ``15 млн ₽``, not raw minor units."""
    if minor is None or not currency:
        return None
    code = currency.upper().strip()
    if code not in ALLOWED_CURRENCIES:
        return None
    decimals = CURRENCY_DECIMALS.get(code, 2)
    major = Decimal(minor) / (Decimal(10) ** decimals)
    symbol = _CURRENCY_DISPLAY.get(code, code)
    if major == major.to_integral_value():
        whole = int(major)
        if whole >= 1_000_000 and whole % 1_000_000 == 0:
            return f"{whole // 1_000_000} млн {symbol}"
        if whole >= 1_000:
            grouped = f"{whole:,}".replace(",", " ")
            return f"{grouped} {symbol}"
        return f"{whole} {symbol}"
    quantized = f"{major:.2f}".rstrip("0").rstrip(".")
    return f"{quantized} {symbol}"


def format_cheque_range(
    min_minor: int | None,
    max_minor: int | None,
    currency: str | None,
    *,
    discuss: bool = False,
) -> str:
    """Investor cheque for cards. Values are stored in minor units."""
    if discuss:
        return "Обсуждаю индивидуально"
    lo = format_ask_amount(min_minor, currency)
    hi = format_ask_amount(max_minor, currency)
    if lo and hi:
        code = (currency or "").upper().strip()
        symbol = _CURRENCY_DISPLAY.get(code, code)
        lo_core = lo[: -len(symbol)].strip() if symbol and lo.endswith(symbol) else lo
        return f"От {lo_core} до {hi}"
    if lo:
        return f"От {lo}"
    if hi:
        return f"До {hi}"
    return ""


def parse_single_amount_major(text: str) -> tuple[int, str] | None:
    """Parse «15000000 RUB» → (minor_units, currency). Rejects NaN/Infinity."""
    parts = text.replace(",", " ").split()
    if len(parts) < 2:
        return None
    currency = parts[1].upper()
    if currency not in ALLOWED_CURRENCIES:
        return None
    major = _finite_decimal(parts[0].replace(" ", ""))
    if major is None:
        return None
    minor = major_units_to_minor(major, currency)
    if minor is None:
        return None
    return minor, currency


def parse_cheque_range_major(text: str) -> tuple[int, int, str] | None:
    """Parse «100000 5000000 RUB» → (min_minor, max_minor, currency)."""
    parts = text.replace(",", " ").split()
    if len(parts) < 3:
        return None
    currency = parts[2].upper()
    if currency not in ALLOWED_CURRENCIES:
        return None
    min_major = _finite_decimal(parts[0])
    max_major = _finite_decimal(parts[1])
    if min_major is None or max_major is None:
        return None
    min_minor = major_units_to_minor(min_major, currency)
    max_minor = major_units_to_minor(max_major, currency)
    if min_minor is None or max_minor is None:
        return None
    if min_minor > max_minor:
        return None
    return min_minor, max_minor, currency


def validate_cheque_bounds(
    *,
    min_minor: int | None,
    max_minor: int | None,
    currency: str | None,
    discuss: bool,
) -> str | None:
    if discuss:
        return None
    if currency is None or currency.upper() not in ALLOWED_CURRENCIES:
        return "Укажите допустимую валюту (RUB, USD, EUR)."
    if min_minor is None and max_minor is None:
        return "Укажите чек или «обсуждаю индивидуально»."
    for value in (min_minor, max_minor):
        if value is not None and (value < MIN_MONEY_MINOR or value > MAX_MONEY_MINOR):
            return "Сумма вне допустимого диапазона."
    if min_minor is not None and max_minor is not None and min_minor > max_minor:
        return "Минимум чека не может быть больше максимума."
    return None


def slugify_startup_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    base = re.sub(r"-+", "-", base).strip("-") or "startup"
    return base[:80]


def can_edit_review_state(state: str) -> bool:
    return state in EDITABLE_STATES


def is_startup_publicly_visible(
    *,
    review_state: str,
    deleted_at: object,
    published_snapshot: object,
) -> bool:
    if deleted_at is not None:
        return False
    if review_state == REVIEW_APPROVED:
        return True
    if review_state == REVIEW_PENDING_CHANGES and published_snapshot:
        return True
    return False


def is_investor_publicly_visible(
    *,
    review_status: str,
    deleted_at: object,
    published_snapshot: object,
) -> bool:
    if deleted_at is not None:
        return False
    if review_status == REVIEW_APPROVED:
        return True
    if review_status == REVIEW_PENDING_CHANGES and published_snapshot:
        return True
    return False


def can_submit_review_state(state: str) -> bool:
    return state in SUBMITTABLE_STATES


def investor_needs_organization(investor_type: str) -> bool:
    return needs_organization_name(investor_type)


def is_known_participant_type(code: str) -> bool:
    return code in KNOWN_PARTICIPANT_TYPES or code in PARTICIPANT_TYPE_LABELS


__all__ = [
    "ALLOWED_CURRENCIES",
    "BOOST_COOLDOWN_HOURS",
    "BOOST_WINDOW_MINUTES",
    "CHEQUE_RELEVANT_TYPES",
    "DESC_MAX",
    "DESC_MIN",
    "FREE_PHOTO_CAP",
    "INVESTOR_TYPES",
    "INVESTOR_TYPE_LABELS",
    "KNOWN_PARTICIPANT_TYPES",
    "MAX_ORG_SEATS",
    "MAX_STARTUP_PHOTOS",
    "MIN_STARTUP_PHOTOS",
    "NAME_MAX",
    "NAME_MIN",
    "ORG_REQUIRED_TYPES",
    "LEGACY_PITCH_MAX",
    "PITCH_MAX",
    "PITCH_MIN",
    "PITCH_PROMPT",
    "PRO_PHOTO_CAP",
    "REVIEW_APPROVED",
    "REVIEW_ARCHIVED",
    "REVIEW_PENDING_CHANGES",
    "REVIEW_SUPERSEDED",
    "REVIEW_CHANGES",
    "REVIEW_DRAFT",
    "REVIEW_REJECTED",
    "REVIEW_SUBMITTED",
    "REVIEW_SUSPENDED",
    "REVIEW_UNDER_REVIEW",
    "NOTE_MAX",
    "CONTACT_POLICY_LABELS",
    "STARTUP_AUDIENCE_LABELS",
    "STARTUP_FUNDRAISING_LABELS",
    "STARTUP_INTENTS",
    "STARTUP_INTENT_LABELS",
    "STARTUP_MONETIZATION_LABELS",
    "STARTUP_PROGRESS_LABELS",
    "STARTUP_USE_OF_FUNDS_LABELS",
    "SUPPORT_MODE_LABELS",
    "boost_cooldown_over",
    "boost_end_time",
    "can_edit_review_state",
    "can_receive_verified_investor_label",
    "can_share_private_contact",
    "can_show_in_investor_feed",
    "can_submit_review_state",
    "canonical_participant_type",
    "cheque_is_relevant",
    "apply_investment_ask_step",
    "format_ask_amount",
    "format_cheque_range",
    "investor_needs_organization",
    "is_known_participant_type",
    "is_listed_project",
    "major_units_to_minor",
    "normalize_https_url",
    "parse_cheque_range_major",
    "parse_single_amount_major",
    "participant_type_label",
    "slugify_startup_name",
    "validate_cheque_bounds",
    "validate_description",
    "validate_investor_preferences",
    "validate_name",
    "validate_pitch",
    "validate_startup_photo_count",
    "validate_strategy",
]
