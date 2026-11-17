"""Telegram-native navigation; no WebApp or inactive payment buttons.

Menu labels are defined once here and imported by every router. A router that
registers a reply-button text with its own literal drifts from this file and the
button silently stops working (the /support bug), so keep the single source.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MENU_RATE = "🚀 Оценивать"
MENU_TOP = "🏆 Топ-10"
MENU_STARTUP = "📝 Мой стартап"
MENU_INVESTOR = "💼 Инвесторам"
MENU_PROFILE = "👤 Мой профиль"
MENU_PREMIUM = "⭐ Premium"
MENU_FAVORITES = "🔖 Избранное"
MENU_REQUESTS = "🤝 Запросы"
MENU_SUPPORT = "🆘 Поддержка"
MENU_SETTINGS = "⚙️ Настройки"

MENU_ROWS: tuple[tuple[str, str], ...] = (
    (MENU_RATE, MENU_TOP),
    (MENU_STARTUP, MENU_INVESTOR),
    (MENU_PROFILE, MENU_PREMIUM),
    (MENU_FAVORITES, MENU_REQUESTS),
    (MENU_SUPPORT, MENU_SETTINGS),
)

CALLBACK_ACCEPT_CONSENT = "consent:accept"
CALLBACK_SHOW_TERMS = "consent:terms"
CALLBACK_SHOW_PRIVACY = "consent:privacy"
CALLBACK_CONSENT_TOGGLE_PREFIX = "consent:t:"
CALLBACK_AGE_PREFIX = "age:"
CALLBACK_CONSENTS_OPEN = "settings:consents"
CALLBACK_CONSENT_REVOKE_PREFIX = "consent:revoke:"
CALLBACK_DELETE_OPEN = "account:delete"
CALLBACK_DELETE_CONFIRM = "account:delete:confirm"
CALLBACK_DELETE_CANCEL = "account:delete:cancel"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in MENU_ROWS],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите раздел RAUNDO",
        is_persistent=True,
    )


CONSENT_LABELS: tuple[tuple[str, str], ...] = (
    ("terms", "Соглашение"),
    ("privacy", "Политика конфиденциальности (ознакомлен)"),
    ("data_processing", "Согласие на обработку персональных данных"),
)


def consent_keyboard(selected: set[str] | None = None) -> InlineKeyboardMarkup:
    """Separate toggles: nothing is pre-selected, accept requires all three."""
    chosen = selected or set()
    rows = []
    for kind, label in CONSENT_LABELS:
        mark = "✅ " if kind in chosen else "☐ "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{label}",
                    callback_data=f"{CALLBACK_CONSENT_TOGGLE_PREFIX}{kind}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📄 Открыть документы",
                url="https://payments.example.invalid/legal",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=CALLBACK_DELETE_OPEN)]
    )
    all_selected = all(kind in chosen for kind, _ in CONSENT_LABELS)
    rows.append(
        [
            InlineKeyboardButton(
                text="Принять и продолжить" if all_selected else "Отметьте все три пункта",
                callback_data=CALLBACK_ACCEPT_CONSENT,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def age_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мне 18+", callback_data=f"{CALLBACK_AGE_PREFIX}18_plus"),
                InlineKeyboardButton(text="Мне 14-17", callback_data=f"{CALLBACK_AGE_PREFIX}14_17"),
            ]
        ]
    )


def adult_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтверждаю 18+", callback_data=f"{CALLBACK_AGE_PREFIX}18_plus")],
            [InlineKeyboardButton(text="Мне 14-17", callback_data=f"{CALLBACK_AGE_PREFIX}14_17")],
        ]
    )


def consents_manage_keyboard(active: dict[str, bool]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if active.get(kind) else '☐'} {label}",
                callback_data=f"{CALLBACK_CONSENT_REVOKE_PREFIX}{kind}",
            )
        ]
        for kind, label in (
            ("profile_publication", "Распространение данных анкеты"),
            ("marketing", "Рекламные уведомления"),
            ("image_advertising", "Использование изображения в рекламе"),
        )
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Отозвать согласие на обработку ПД",
                callback_data="consent:revoke:data_processing",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=CALLBACK_DELETE_OPEN)]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="profile:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def onboarding_keyboard(*, consent: bool, age_required: bool) -> InlineKeyboardMarkup:
    """One explicit consent checkbox plus an age choice; nothing pre-selected."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if consent else '☐'} Согласие на обработку ПД",
                callback_data="onb:consent",
            )
        ]
    ]
    if age_required:
        rows.append(
            [
                InlineKeyboardButton(text="Мне 18+", callback_data=f"{CALLBACK_AGE_PREFIX}18_plus"),
                InlineKeyboardButton(text="Мне 14-17", callback_data=f"{CALLBACK_AGE_PREFIX}14_17"),
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="Продолжить", callback_data="onb:continue")]
        )
    rows.append(
        [InlineKeyboardButton(text="📄 Открыть все документы", url="https://payments.example.invalid/legal")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def data_revoke_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить отзыв", callback_data="consent:data:confirm"
                )
            ],
            [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=CALLBACK_DELETE_OPEN)],
            [InlineKeyboardButton(text="Отмена", callback_data="consent:data:cancel")],
        ]
    )


def publication_consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Согласен на публикацию",
                    callback_data="consent:pub:grant",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Условия распространения",
                    url="https://payments.example.invalid/legal/profile-publication",
                )
            ],
        ]
    )


def deletion_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Удалить аккаунт", callback_data=CALLBACK_DELETE_CONFIRM)],
            [InlineKeyboardButton(text="Отмена", callback_data=CALLBACK_DELETE_CANCEL)],
        ]
    )
