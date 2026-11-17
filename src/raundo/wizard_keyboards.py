"""Reply/inline keyboards for profile wizards."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from raundo.domain import (
    STARTUP_AUDIENCE_LABELS,
    STARTUP_FUNDRAISING_LABELS,
    STARTUP_INTENT_LABELS,
    STARTUP_MONETIZATION_LABELS,
    STARTUP_PROGRESS_LABELS,
    STARTUP_USE_OF_FUNDS_LABELS,
    SUPPORT_MODE_LABELS,
)
from raundo.keyboard_grid import grid_markup, single_choice_grid
from raundo.ui_labels import PARTICIPANT_TYPE_BUTTON_LABELS

BTN_BACK = "⬅️ Назад"
BTN_CANCEL = "❌ Отмена"
BTN_SAVE = "💾 Черновик"
BTN_SKIP = "Пропустить"
BTN_CLEAR_ASK = "Убрать сумму"
BTN_NO_LINK = "Нет ссылки пока"
BTN_PHOTOS_DONE = "✅ Готово с фото"
BTN_SUBMIT = "✅ Отправить на проверку"
BTN_EDIT = "✏️ Редактировать анкету"
BTN_VIEW_CARD = "👁 Посмотреть карточку"
BTN_DELETE_PROFILE = "🗑 Удалить анкету"
BTN_CONFIRM_DELETE = "🗑 Подтвердить удаление"
BTN_CANCEL_DELETE = "Отмена"
BTN_MORE = "Подробнее"
BTN_CONTINUE = "▶️ Продолжить"
BTN_CREATE_STARTUP = "➕ Создать стартап"
BTN_CREATE_INVESTOR = "➕ Создать анкету"
BTN_CHECK_INDIVIDUAL = "Обсуждаю индивидуально"
BTN_PREMIUM_VIEW = "⭐ Посмотреть Premium"
BTN_CONTINUE_FREE = "✅ Продолжить бесплатно"
BTN_BACK_TO_FORM = "◀️ Назад к анкете"

CB_SEC = "w:sec:"  # + code
CB_STG = "w:stg:"
CB_INTENT = "w:int:"
CB_AUD = "w:aud:"
CB_PROG = "w:prg:"
CB_MON = "w:mon:"
CB_FUND = "w:fnd:"
CB_UOF = "w:uof:"
CB_ITYPE = "w:ity:"
CB_SMOC = "w:sm:"  # support mode
CB_DONE_SEC = "w:done:sec"
CB_DONE_STG = "w:done:stg"
CB_DONE_INT = "w:done:int"
CB_DONE_AUD = "w:done:aud"
CB_DONE_PROG = "w:done:prg"
CB_DONE_UOF = "w:done:uof"
CB_DONE_SM = "w:done:sm"
CB_CONTACT = "w:ctc:"  # relay|username
CB_GEO = "w:geo:"  # ru|world
CB_EDIT = "w:edt:"  # field code or back
CB_IEDT = "w:ied:"  # investor edit field


def wizard_nav(
    *,
    include_save: bool = True,
    include_skip: bool = False,
    include_clear_ask: bool = False,
) -> ReplyKeyboardMarkup:
    row: list[KeyboardButton] = [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]
    rows = [row]
    extra: list[KeyboardButton] = []
    if include_save:
        extra.append(KeyboardButton(text=BTN_SAVE))
    if include_skip:
        extra.append(KeyboardButton(text=BTN_SKIP))
    if include_clear_ask:
        extra.append(KeyboardButton(text=BTN_CLEAR_ASK))
    if extra:
        if len(extra) <= 2:
            rows.append(extra)
        else:
            rows.append(extra[:2])
            rows.append(extra[2:])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def photos_nav(*, include_skip: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton(text=BTN_PHOTOS_DONE)]]
    if include_skip:
        rows.insert(0, [KeyboardButton(text=BTN_SKIP)])
    rows.append([KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)])
    rows.append([KeyboardButton(text=BTN_SAVE)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def links_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NO_LINK)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)],
            [KeyboardButton(text=BTN_SAVE)],
        ],
        resize_keyboard=True,
    )


def check_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CHECK_INDIVIDUAL)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)],
            [KeyboardButton(text=BTN_SAVE)],
        ],
        resize_keyboard=True,
    )


def preview_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SUBMIT)],
            [KeyboardButton(text=BTN_MORE)],
            [KeyboardButton(text=BTN_EDIT), KeyboardButton(text=BTN_SAVE)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def premium_offer_nav() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PREMIUM_VIEW)],
            [KeyboardButton(text=BTN_CONTINUE_FREE)],
            [KeyboardButton(text=BTN_BACK_TO_FORM)],
        ],
        resize_keyboard=True,
    )


def hub_startup_keyboard(*, has_draft: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton(text=BTN_CREATE_STARTUP)]]
    if has_draft:
        rows.insert(0, [KeyboardButton(text=BTN_CONTINUE)])
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def manage_startup_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_VIEW_CARD)],
            [KeyboardButton(text=BTN_EDIT)],
            [KeyboardButton(text=BTN_DELETE_PROFILE)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def delete_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM_DELETE)],
            [KeyboardButton(text=BTN_CANCEL_DELETE)],
        ],
        resize_keyboard=True,
    )


def hub_investor_keyboard(*, has_draft: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [[KeyboardButton(text=BTN_CREATE_INVESTOR)]]
    if has_draft:
        rows.insert(0, [KeyboardButton(text=BTN_CONTINUE)])
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def manage_investor_keyboard() -> ReplyKeyboardMarkup:
    return manage_startup_keyboard()


def toggle_markup(
    *,
    items: list[tuple[str, str]],
    selected: set[str],
    prefix: str,
    done_callback: str,
    columns: int = 1,
) -> InlineKeyboardMarkup:
    return grid_markup(
        items,
        prefix=prefix,
        columns=columns,
        selected=selected,
        done_callback=done_callback,
    )


def sector_toggle_keyboard(
    sectors: list[tuple[str, str]], selected: set[str], *, columns: int = 3
) -> InlineKeyboardMarkup:
    return toggle_markup(
        items=sectors,
        selected=selected,
        prefix=CB_SEC,
        done_callback=CB_DONE_SEC,
        columns=columns,
    )


def stage_toggle_keyboard(
    stages: list[tuple[str, str]], selected: set[str], *, columns: int = 2
) -> InlineKeyboardMarkup:
    return toggle_markup(
        items=stages,
        selected=selected,
        prefix=CB_STG,
        done_callback=CB_DONE_STG,
        columns=columns,
    )


def intent_toggle_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    items = [(code, STARTUP_INTENT_LABELS[code]) for code in STARTUP_INTENT_LABELS]
    return toggle_markup(
        items=items, selected=selected, prefix=CB_INTENT, done_callback=CB_DONE_INT, columns=2
    )


def audience_toggle_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    items = list(STARTUP_AUDIENCE_LABELS.items())
    return toggle_markup(
        items=items, selected=selected, prefix=CB_AUD, done_callback=CB_DONE_AUD, columns=2
    )


def progress_toggle_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    items = list(STARTUP_PROGRESS_LABELS.items())
    return toggle_markup(
        items=items, selected=selected, prefix=CB_PROG, done_callback=CB_DONE_PROG, columns=2
    )


def monetization_keyboard() -> InlineKeyboardMarkup:
    return single_choice_grid(list(STARTUP_MONETIZATION_LABELS.items()), prefix=CB_MON, columns=1)


def fundraising_keyboard() -> InlineKeyboardMarkup:
    return single_choice_grid(list(STARTUP_FUNDRAISING_LABELS.items()), prefix=CB_FUND, columns=2)


def use_of_funds_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    items = list(STARTUP_USE_OF_FUNDS_LABELS.items())
    return toggle_markup(
        items=items, selected=selected, prefix=CB_UOF, done_callback=CB_DONE_UOF, columns=2
    )


def investor_type_keyboard() -> InlineKeyboardMarkup:
    items = [(code, PARTICIPANT_TYPE_BUTTON_LABELS[code]) for code in PARTICIPANT_TYPE_BUTTON_LABELS]
    return single_choice_grid(items, prefix=CB_ITYPE, columns=2)


def support_mode_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    items = [(code, label) for code, label in SUPPORT_MODE_LABELS.items()]
    return toggle_markup(
        items=items, selected=selected, prefix=CB_SMOC, done_callback=CB_DONE_SM, columns=2
    )


def contact_policy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Через RAUNDO",
                    callback_data=f"{CB_CONTACT}relay",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔒 По взаимному согласию",
                    callback_data=f"{CB_CONTACT}username",
                )
            ],
        ]
    )


def edit_fields_keyboard() -> InlineKeyboardMarkup:
    """Inline field picker after «Изменить». Preview ReplyKeyboard stays untouched."""
    rows = [
        ("name", "Название"),
        ("pitch", "Короткий питч"),
        ("description", "Полное описание"),
        ("sectors", "Направления"),
        ("stage", "Стадия"),
        ("links", "Сайт / ссылка"),
        ("photos", "Фотографии"),
        ("intents", "Что ищем"),
        ("audience", "Аудитория"),
        ("progress", "Прогресс"),
        ("monetization", "Монетизация"),
        ("ask", "Инвестиционный запрос"),
        ("fundraising", "Раунд / средства"),
        ("geography", "География"),
        ("founder", "Команда"),
        ("contact", "Связь"),
        ("back", "← К карточке"),
    ]
    return grid_markup(
        rows,
        prefix=CB_EDIT,
        columns=2,
        selected=None,
        done_callback=None,
    )


def investor_edit_fields_keyboard() -> InlineKeyboardMarkup:
    rows = [
        ("photo", "Фото / логотип"),
        ("name", "Имя"),
        ("type", "Тип участия"),
        ("org", "Организация"),
        ("position", "Должность"),
        ("strategy", "Стратегия"),
        ("sectors", "Направления"),
        ("stages", "Стадии"),
        ("check", "Инвестиционный чек"),
        ("geography", "География"),
        ("link", "Публичная ссылка"),
        ("contact", "Связь"),
        ("back", "← К карточке"),
    ]
    return grid_markup(rows, prefix=CB_IEDT, columns=2, selected=None, done_callback=None)


def geography_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Россия", callback_data=f"{CB_GEO}ru")],
            [InlineKeyboardButton(text="Весь мир", callback_data=f"{CB_GEO}world")],
        ]
    )


def single_choice_codes(
    items: list[tuple[str, str]], prefix: str, *, columns: int = 3
) -> InlineKeyboardMarkup:
    return single_choice_grid(items, prefix=prefix, columns=columns)
