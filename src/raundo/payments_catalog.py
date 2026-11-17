"""Server-side payment catalog for Premium.

RUB prices come from the existing preliminary catalog. Stars prices are a
separate explicit configuration and are NOT approved for live use: they exist so
the Stars branch can be built and tested, and stay blocked until both
`PAYMENTS_STARS_ENABLED` and `PAYMENTS_STARS_PRICES_APPROVED` are set.
"""

from __future__ import annotations

from dataclasses import dataclass

from raundo.premium_catalog import (
    DURATION_HOURS,
    DURATION_TITLES,
    PLAN_AUDIENCES,
    PLAN_CODES,
    PLAN_TITLES,
    PRICES_MINOR,
    format_price,
    is_known_duration,
    is_known_plan,
)

PLAN_VERSION = 1

# Short, honest benefits shown on the checkout page. Only real or explicitly
# "will appear later" statements; no invented features.
PLAN_BENEFITS: dict[str, tuple[str, ...]] = {
    "community_plus": (
        "Расширенные коллекции избранного",
        "История просмотренных проектов",
        "Уведомления по интересам (появятся позже)",
    ),
    "startup_pro": (
        "До 3 стартапов вместо 1",
        "До 3 фотографий в карточке",
        "Продвижение и аналитика (появятся позже)",
    ),
    "investor_pro": (
        "До 3 инвесторских профилей вместо 1",
        "Сохранённые стратегии поиска",
        "Радар проектов (появится позже)",
    ),
    "organization_pro": (
        "До 3 собственных организаций вместо 1",
        "До 5 мест для подтверждённых представителей",
        "Командные инструменты (появятся позже)",
    ),
}

# PROVISIONAL Stars prices (whole XTR). Not approved for live charging; a real
# price list must be approved by the owner and set via PAYMENTS_STARS_* flags.
PROVISIONAL_STARS_PRICES: dict[str, dict[str, int]] = {
    "community_plus": {"3d": 50, "7d": 100, "30d": 350},
    "startup_pro": {"3d": 150, "7d": 300, "30d": 900},
    "investor_pro": {"3d": 500, "7d": 1000, "30d": 3000},
    "organization_pro": {"3d": 1000, "7d": 2000, "30d": 6000},
}

CURRENCY_RUB = "RUB"
CURRENCY_XTR = "XTR"


@dataclass(frozen=True)
class PaymentOffer:
    plan_code: str
    plan_version: int
    title: str
    audience: str
    duration_code: str
    duration_hours: int
    duration_title: str
    amount_minor: int
    currency: str
    price_label: str
    benefits: tuple[str, ...]
    stars_amount: int | None


def rub_offer(plan_code: str, duration_code: str) -> PaymentOffer | None:
    if not is_known_plan(plan_code) or not is_known_duration(duration_code):
        return None
    minor = PRICES_MINOR[plan_code][duration_code]
    return PaymentOffer(
        plan_code=plan_code,
        plan_version=PLAN_VERSION,
        title=PLAN_TITLES[plan_code],
        audience=PLAN_AUDIENCES[plan_code],
        duration_code=duration_code,
        duration_hours=DURATION_HOURS[duration_code],
        duration_title=DURATION_TITLES[duration_code],
        amount_minor=minor,
        currency=CURRENCY_RUB,
        price_label=format_price(minor),
        benefits=PLAN_BENEFITS[plan_code],
        stars_amount=PROVISIONAL_STARS_PRICES[plan_code].get(duration_code),
    )


def all_rub_offers() -> tuple[PaymentOffer, ...]:
    return tuple(
        offer
        for plan in PLAN_CODES
        for duration in DURATION_HOURS
        if (offer := rub_offer(plan, duration)) is not None
    )


def stars_amount(plan_code: str, duration_code: str) -> int | None:
    if not is_known_plan(plan_code) or not is_known_duration(duration_code):
        return None
    return PROVISIONAL_STARS_PRICES[plan_code].get(duration_code)


def format_amount(amount_minor: int, currency: str) -> str:
    if currency == CURRENCY_RUB:
        return format_price(amount_minor)
    return f"{amount_minor} Stars"
