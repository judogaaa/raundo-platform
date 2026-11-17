"""Premium catalog: 4 plans × 3 durations (owner decision D014, 2026-10-26).

Prices are preliminary RUB guides stored in minor units (kopecks).
No real charges, invoices or Stars conversion happen at this stage.
"""

from __future__ import annotations

PLAN_COMMUNITY_PLUS = "community_plus"
PLAN_STARTUP_PRO = "startup_pro"
PLAN_INVESTOR_PRO = "investor_pro"
PLAN_ORGANIZATION_PRO = "organization_pro"

PLAN_CODES: tuple[str, ...] = (
    PLAN_COMMUNITY_PLUS,
    PLAN_STARTUP_PRO,
    PLAN_INVESTOR_PRO,
    PLAN_ORGANIZATION_PRO,
)

PLAN_TITLES: dict[str, str] = {
    PLAN_COMMUNITY_PLUS: "👑 Community PLUS",
    PLAN_STARTUP_PRO: "🚀 Startup PRO",
    PLAN_INVESTOR_PRO: "💼 Investor PRO",
    PLAN_ORGANIZATION_PRO: "🏢 Organization PRO",
}

PLAN_AUDIENCES: dict[str, str] = {
    PLAN_COMMUNITY_PLUS: "Для обычных участников сообщества",
    PLAN_STARTUP_PRO: "Для основателей стартапов",
    PLAN_INVESTOR_PRO: "Для частных инвесторов и бизнес-ангелов",
    PLAN_ORGANIZATION_PRO: "Для фондов, акселераторов и корпоративных команд",
}

DURATION_3D = "3d"
DURATION_7D = "7d"
DURATION_30D = "30d"

DURATION_CODES: tuple[str, ...] = (DURATION_3D, DURATION_7D, DURATION_30D)

# Real durations in hours counted from activation, UTC (72/168/720).
DURATION_HOURS: dict[str, int] = {
    DURATION_3D: 72,
    DURATION_7D: 168,
    DURATION_30D: 720,
}

DURATION_TITLES: dict[str, str] = {
    DURATION_3D: "3 дня",
    DURATION_7D: "7 дней",
    DURATION_30D: "30 дней",
}

# Preliminary price guides in minor units (kopecks). Never charged at this stage.
PRICES_MINOR: dict[str, dict[str, int]] = {
    PLAN_COMMUNITY_PLUS: {DURATION_3D: 4_900, DURATION_7D: 9_900, DURATION_30D: 29_900},
    PLAN_STARTUP_PRO: {DURATION_3D: 14_900, DURATION_7D: 29_900, DURATION_30D: 79_900},
    PLAN_INVESTOR_PRO: {DURATION_3D: 69_900, DURATION_7D: 149_000, DURATION_30D: 499_000},
    PLAN_ORGANIZATION_PRO: {DURATION_3D: 149_000, DURATION_7D: 299_000, DURATION_30D: 999_000},
}

PRICE_CURRENCY = "RUB"


def is_known_plan(plan_code: str) -> bool:
    return plan_code in PLAN_CODES


def is_known_duration(duration_code: str) -> bool:
    return duration_code in DURATION_CODES


def price_minor(plan_code: str, duration_code: str) -> int | None:
    """Price in minor units, or None when the combination is unknown."""
    if not is_known_plan(plan_code) or not is_known_duration(duration_code):
        return None
    return PRICES_MINOR[plan_code][duration_code]


def format_price(minor: int) -> str:
    """Minor units (kopecks) → human RUB string, e.g. 1490 → «1 490 ₽»."""
    if minor % 100 == 0:
        return f"{minor // 100:,} ₽".replace(",", " ")
    return f"{minor / 100:.2f} ₽".replace(".", ",")


# Feature status markers shown in Telegram. Nothing is claimed live without
# its real dependency; «stored» means storage/rules are ready behind the flag.
STATUS_LIVE = "live"  # работает уже сейчас
STATUS_STORED = "stored"  # хранилище и правила готовы, интерфейс появится позже
STATUS_PENDING_ADMIN = "pending_admin"  # ждёт admin-модуль следующей очереди
STATUS_PENDING_DECISION = "pending_decision"  # требует отдельного решения владельца

STATUS_MARKERS: dict[str, str] = {
    STATUS_LIVE: "✅",
    STATUS_STORED: "📦",
    STATUS_PENDING_ADMIN: "🛡",
    STATUS_PENDING_DECISION: "⚙️",
}

STATUS_LEGEND: str = (
    "✅ работает уже сейчас.\n"
    "📦 данные и правила готовы, интерфейс появится в следующих обновлениях.\n"
    "🛡 появится в следующей очереди административных инструментов.\n"
    "⚙️ ожидает отдельного решения."
)

# (текст возможности, статус). Группируется по тарифам для «Что входит».
PLAN_FEATURES: dict[str, list[tuple[str, str]]] = {
    PLAN_COMMUNITY_PLUS: [
        ("Расширенные коллекции избранного (несколько списков)", STATUS_STORED),
        ("История просмотренных и оценённых проектов", STATUS_STORED),
        ("Персональные тематические подборки", STATUS_STORED),
        ("Уведомления о новых проектах по интересам", STATUS_STORED),
    ],
    PLAN_STARTUP_PRO: [
        ("До 3 фотографий в карточке вместо 1", STATUS_LIVE),
        ("Ежедневное продвижение на 30 минут", STATUS_STORED),
        ("Аналитика показов и интереса", STATUS_STORED),
        ("Расширенные материалы и оформление карточки", STATUS_PENDING_DECISION),
    ],
    PLAN_INVESTOR_PRO: [
        ("Сохранённые стратегии поиска", STATUS_STORED),
        ("Радар новых подходящих проектов", STATUS_STORED),
        ("Персональные тематические подборки", STATUS_STORED),
        ("Сравнение нескольких стартапов", STATUS_STORED),
        ("Приватные заметки о проектах", STATUS_STORED),
        ("Личная воронка: интерес → общаемся", STATUS_STORED),
    ],
    PLAN_ORGANIZATION_PRO: [
        (
            "До 5 мест для подтверждённых представителей (правила на сервере; командный интерфейс позже)",
            STATUS_STORED,
        ),
        ("Командное рабочее пространство", STATUS_STORED),
        ("Общие подборки и распределение проектов", STATUS_STORED),
        ("Приватные комментарии сотрудников", STATUS_STORED),
        ("Командная воронка и критерии отбора", STATUS_STORED),
        ("Командная аналитика", STATUS_STORED),
        ("Приём заявок на инвестиции/программы", STATUS_PENDING_ADMIN),
    ],
}

PAYMENT_DISABLED_NOTE = (
    "💳 Оплата картой или через СБП и Telegram Stars подключается после "
    "коммерческого и юридического согласования. В локальном режиме доступен "
    "только тестовый checkout без списания денег."
)


def all_offers() -> tuple[tuple[str, str, int, int], ...]:
    """All 12 offers: (plan_code, duration_code, duration_hours, price_minor)."""
    return tuple(
        (plan, duration, DURATION_HOURS[duration], price_minor(plan, duration) or 0)
        for plan in PLAN_CODES
        for duration in DURATION_CODES
    )
