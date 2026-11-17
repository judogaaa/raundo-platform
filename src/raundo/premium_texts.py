"""Premium Telegram texts. No payment promises, no fake success."""

from __future__ import annotations

from raundo.premium_catalog import (
    DURATION_CODES,
    DURATION_HOURS,
    DURATION_TITLES,
    PAYMENT_DISABLED_NOTE,
    PLAN_AUDIENCES,
    PLAN_FEATURES,
    PLAN_TITLES,
    PRICES_MINOR,
    STATUS_LEGEND,
    STATUS_MARKERS,
    format_price,
)

PREMIUM_INTRO = (
    "⭐ Premium RAUNDO\n\n"
    "Четыре тарифа для разных ролей: Community PLUS, Startup PRO, "
    "Investor PRO и Organization PRO. У каждого три срока действия.\n\n"
    "Основное в RAUNDO бесплатно: просмотр и оценка стартапов, избранное, "
    "честные рейтинги и запросы на знакомство."
)

PREMIUM_PAYMENT_DISABLED = PAYMENT_DISABLED_NOTE

PREMIUM_LEGEND = "Обозначения:\n" + STATUS_LEGEND

PREMIUM_MINE_EMPTY = (
    "У вас пока нет подписок. Это нормально: всё основное в RAUNDO бесплатно.\n\n"
    "Когда появится оплата, здесь будут видны активные и прошлые подписки."
)

PREMIUM_HOW = (
    "ℹ️ Как работает PRO\n\n"
    "1. Срок считается по UTC: ровно 72, 168 или 720 часов от активации.\n"
    "2. Права действуют по сроку подписки и проверяются на сервере.\n"
    "3. После окончания данные не удаляются: фото и командные записи сохраняются, "
    "премиальные действия просто блокируются.\n"
    "4. PRO не покупает верификацию: одобрение стартапа, инвестора или "
    "организации проходит только ручную проверку администрацией.\n"
    "5. Никакого автопродления и автосписаний: оплата будет разовой, когда "
    "её подключат.\n"
    "6. Продление добавляет срок к действующей подписке без разрыва."
)

PREMIUM_EXPIRY = (
    "⏳ Срок действия\n\n"
    "3 дня: платный ознакомительный период (72 часа).\n"
    "7 дней: неделя доступа (168 часов).\n"
    "30 дней: месяц доступа (720 часов).\n\n"
    "Отсчёт начинается с момента активации (UTC). Истечение проверяется "
    "на сервере при каждом действии, даже если бот был выключен."
)

PREMIUM_COMPARE_HEADER = "📊 Сравнение тарифов (цены предварительные, в рублях)"

PREMIUM_SUB_LOOK = (
    "Оформление появится здесь после подключения платежей.\n"
    "Сейчас деньги не списываются, подписка не создаётся нажатием кнопки."
)

PREMIUM_DEV_HINT = (
    "🧪 Тестовая выдача (только для администраторов)\n\n"
    "Выдаёт подписку с пометкой «тест», не создаёт платёж и не даёт "
    "подтверждённых статусов. Механизм включается отдельным флагом на сервере "
    "и доступен только администраторам из списка доступа."
)


def plan_overview(plan_code: str) -> str:
    title = PLAN_TITLES[plan_code]
    audience = PLAN_AUDIENCES[plan_code]
    lines = [f"{title}\n\n{audience}\n", "Сроки и цены:"]
    for duration in DURATION_CODES:
        price = format_price(PRICES_MINOR[plan_code][duration])
        hours = DURATION_HOURS[duration]
        lines.append(f"• {DURATION_TITLES[duration]} ({hours} ч): {price}")
    lines.append("")
    lines.append(PREMIUM_PAYMENT_DISABLED)
    return "\n".join(lines)


def plan_features_text(plan_code: str) -> str:
    title = PLAN_TITLES[plan_code]
    lines = [f"{title}: что входит\n"]
    for feature, status in PLAN_FEATURES[plan_code]:
        lines.append(f"{STATUS_MARKERS[status]} {feature}")
    lines.append("")
    lines.append(PREMIUM_LEGEND)
    return "\n".join(lines)


def compare_text() -> str:
    lines = [PREMIUM_COMPARE_HEADER, ""]
    for plan_code in (
        "community_plus",
        "startup_pro",
        "investor_pro",
        "organization_pro",
    ):
        title = PLAN_TITLES[plan_code]
        prices = " · ".join(
            f"{DURATION_TITLES[d]}: {format_price(PRICES_MINOR[plan_code][d])}"
            for d in DURATION_CODES
        )
        lines.append(f"{title}\n{prices}\n")
    lines.append("Все тарифы дают одинаковый набор функций. Отличается только срок.")
    return "\n".join(lines)


def subscription_line(row: dict) -> str:
    """Human row for «Мои подписки» from a safe, already-shaped dict."""
    state = "✅ активна" if row["active"] else "⏱ истекла или отменена"
    price = format_price(row["price_minor"])
    return (
        f"• {row['plan_title']}: {DURATION_TITLES.get(row['duration'], row['duration'])}\n"
        f"  {state}, до {row['expires_at']:%d.%m.%Y %H:%M} UTC · {price}"
        + (" · тестовая" if row["is_test"] else "")
    )
