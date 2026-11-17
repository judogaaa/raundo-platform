"""Founder startup wizard — full form, photos, draft/resume/submit."""

from __future__ import annotations

import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.cards.cover_present import resolve_card_photo
from raundo.cards.startup_card import (
    build_startup_card_view,
    compact_startup_html,
    details_startup_html,
)
from raundo.config import get_settings  # noqa: F401 - tests patch this module attribute
from raundo.domain import (
    PITCH_MAX,
    PITCH_PROMPT,
    REVIEW_APPROVED,
    REVIEW_CHANGES,
    REVIEW_DRAFT,
    REVIEW_PENDING_CHANGES,
    REVIEW_REJECTED,
    REVIEW_SUBMITTED,
    REVIEW_UNDER_REVIEW,
    STARTUP_AUDIENCE_LABELS,
    STARTUP_INTENT_LABELS,
    STARTUP_PROGRESS_LABELS,
    STARTUP_USE_OF_FUNDS_LABELS,
    apply_investment_ask_step,
    can_edit_review_state,
    format_ask_amount,
    normalize_https_url,
    validate_description,
    validate_name,
    validate_pitch,
)
from raundo.keyboards import main_menu, publication_consent_keyboard
from raundo.models.user import User
from raundo.premium_catalog import PLAN_STARTUP_PRO, PLAN_TITLES
from raundo.premium_texts import PREMIUM_INTRO, plan_features_text
from raundo.repositories.catalog import CatalogRepository
from raundo.routers.premium import premium_root_keyboard
from raundo.routers.publication_text import PUBLICATION_CONSENT_TEXT
from raundo.services.analytics import AnalyticsService
from raundo.services.consents import ConsentService, has_publication_consent
from raundo.services.notifications import NotificationService
from raundo.services.premium import PremiumService
from raundo.services.profile_limits import (
    KIND_STARTUP,
    ProfileLimitError,
    ensure_can_create,
    limits_for_user,
)
from raundo.services.startups import StartupService, StartupServiceError
from raundo.state.startup import StartupWizard
from raundo.texts import NEED_CONSENT
from raundo.ui_labels import review_state_label
from raundo.wizard_copy import (
    DELETE_PROFILE_PROMPT,
    DELETED_STARTUP,
    PREMIUM_ALREADY_ACTIVE,
    PREMIUM_OFFER_STARTUP,
    REEDIT_MODERATION_NOTICE,
    STARTUP_ASK,
    STARTUP_AUDIENCE,
    STARTUP_AUDIENCE_NOTE,
    STARTUP_CONTACT,
    STARTUP_DESCRIPTION,
    STARTUP_EDIT_PICK,
    STARTUP_FOUNDER,
    STARTUP_FUNDRAISING,
    STARTUP_GEO,
    STARTUP_HUB_EMPTY,
    STARTUP_INTENTS,
    STARTUP_LINKS,
    STARTUP_MANAGE_HEADER,
    STARTUP_MONETIZATION,
    STARTUP_MONETIZATION_NOTE,
    STARTUP_NAME,
    STARTUP_PRIMARY,
    STARTUP_PROGRESS,
    STARTUP_SECONDARY,
    STARTUP_STAGE,
    STARTUP_USE_OF_FUNDS,
    SUBMITTED_STARTUP,
)
from raundo.wizard_flow import next_startup_step, previous_startup_step
from raundo.wizard_keyboards import (
    BTN_BACK,
    BTN_BACK_TO_FORM,
    BTN_CANCEL,
    BTN_CANCEL_DELETE,
    BTN_CLEAR_ASK,
    BTN_CONFIRM_DELETE,
    BTN_CONTINUE,
    BTN_CONTINUE_FREE,
    BTN_CREATE_STARTUP,
    BTN_DELETE_PROFILE,
    BTN_EDIT,
    BTN_MORE,
    BTN_NO_LINK,
    BTN_PHOTOS_DONE,
    BTN_PREMIUM_VIEW,
    BTN_SAVE,
    BTN_SKIP,
    BTN_SUBMIT,
    BTN_VIEW_CARD,
    CB_AUD,
    CB_CONTACT,
    CB_DONE_AUD,
    CB_DONE_INT,
    CB_DONE_PROG,
    CB_DONE_SEC,
    CB_DONE_UOF,
    CB_EDIT,
    CB_FUND,
    CB_GEO,
    CB_INTENT,
    CB_MON,
    CB_PROG,
    CB_SEC,
    CB_STG,
    CB_UOF,
    audience_toggle_keyboard,
    contact_policy_keyboard,
    delete_confirm_keyboard,
    edit_fields_keyboard,
    fundraising_keyboard,
    geography_keyboard,
    intent_toggle_keyboard,
    links_nav,
    manage_startup_keyboard,
    monetization_keyboard,
    photos_nav,
    premium_offer_nav,
    preview_nav,
    progress_toggle_keyboard,
    sector_toggle_keyboard,
    single_choice_codes,
    use_of_funds_keyboard,
    wizard_nav,
)

MENU_STARTUP = "📝 Мой стартап"


def _plan_date(value: str | None) -> str:
    """ISO datetime/date from limits → DD.MM.YYYY without time or tz noise."""
    raw = (value or "").strip()
    if len(raw) < 10:
        return "дата не указана"
    year, month, day = raw[:10].split("-")
    return f"{day}.{month}.{year}"

_NAV_BUTTONS = frozenset(
    {BTN_BACK, BTN_CANCEL, BTN_SAVE, BTN_SKIP, BTN_CLEAR_ASK, BTN_NO_LINK, BTN_PHOTOS_DONE}
)


def _pitch_step_prompt(startup) -> str:
    current = (getattr(startup, "one_liner", None) or "").strip()
    if current and len(current) > PITCH_MAX:
        return (
            f"{PITCH_PROMPT}\n\n"
            f"Сейчас сохранено {len(current)} символов. Сократите до {PITCH_MAX} "
            "перед отправкой на проверку. Можно продолжить черновик и вернуться позже."
        )
    if current and len(current) < 50:
        return (
            f"{PITCH_PROMPT}\n\n"
            f"Сейчас {len(current)} символов. Дополните до минимум 50."
        )
    return PITCH_PROMPT


async def _require_consent(message: Message, session: AsyncSession, db_user: User) -> bool:
    ok = await ConsentService(session).has_required_consents(
        db_user.id
    )
    if not ok:
        await message.answer(NEED_CONSENT)
    return ok


async def _load_startup(session: AsyncSession, state: FSMContext, db_user: User):
    data = await state.get_data()
    sid = data.get("startup_id")
    if not sid:
        return None
    return await StartupService(session).get_owned(uuid.UUID(sid), db_user.id)


async def _load_editable_startup(session: AsyncSession, state: FSMContext, db_user: User):
    """Owned startup that is still in an editable review_state (server-side guard)."""
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        return None
    await StartupService(session).ensure_editable(startup)
    return startup


async def _editable_for_message(
    message: Message, session: AsyncSession, state: FSMContext, db_user: User
):
    try:
        return await _load_editable_startup(session, state, db_user)
    except StartupServiceError as exc:
        await message.answer(exc.message)
        return None


async def _editable_for_callback(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, db_user: User
):
    try:
        startup = await _load_editable_startup(session, state, db_user)
    except StartupServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return None
    if startup is None:
        await callback.answer()
        return None
    return startup


async def _ask(message: Message, text: str, *, nav=None) -> None:
    await message.answer(text, reply_markup=nav or wizard_nav())


async def _prepare_startup_write(session: AsyncSession, startup) -> None:
    if startup.review_state == REVIEW_APPROVED:
        await StartupService(session).prepare_owner_field_write(startup)


async def _after_edit_or_continue(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    *,
    continue_fn,
) -> None:
    """If field was opened from «Изменить», return to preview; else run normal next step."""
    data = await state.get_data()
    if data.get("edit_mode"):
        await state.update_data(edit_mode=False)
        await state.set_state(StartupWizard.preview)
        startup = await _load_startup(session, state, db_user)
        if startup is not None:
            await StartupService(session).register_owner_change(startup)
        await message.answer("Сохранено.", reply_markup=preview_nav())
        if startup is not None:
            await _show_preview(message, session, startup)
        return
    await continue_fn()


async def _after_edit_or_continue_cb(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    *,
    continue_fn,
) -> None:
    data = await state.get_data()
    if data.get("edit_mode"):
        await state.update_data(edit_mode=False)
        await state.set_state(StartupWizard.preview)
        await callback.answer("Сохранено")
        startup = await _load_startup(session, state, db_user)
        if startup is not None:
            await StartupService(session).register_owner_change(startup)
        if callback.message is not None:
            await callback.message.answer("Сохранено.", reply_markup=preview_nav())
            if startup is not None:
                await _show_preview(callback.message, session, startup)
        return
    await continue_fn()


async def _prompt_startup_state(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    step: str,
    startup=None,
) -> None:
    """Send the prompt for a given FSM state (resume / back)."""
    state_obj = {
        StartupWizard.hub.state: StartupWizard.hub,
        StartupWizard.name.state: StartupWizard.name,
        StartupWizard.pitch.state: StartupWizard.pitch,
        StartupWizard.description.state: StartupWizard.description,
        StartupWizard.primary_sector.state: StartupWizard.primary_sector,
        StartupWizard.secondary_sectors.state: StartupWizard.secondary_sectors,
        StartupWizard.stage.state: StartupWizard.stage,
        StartupWizard.links.state: StartupWizard.links,
        StartupWizard.photos.state: StartupWizard.photos,
        StartupWizard.intents.state: StartupWizard.intents,
        StartupWizard.audience.state: StartupWizard.audience,
        StartupWizard.audience_note.state: StartupWizard.audience_note,
        StartupWizard.progress.state: StartupWizard.progress,
        StartupWizard.monetization.state: StartupWizard.monetization,
        StartupWizard.monetization_note.state: StartupWizard.monetization_note,
        StartupWizard.investment_ask.state: StartupWizard.investment_ask,
        StartupWizard.fundraising.state: StartupWizard.fundraising,
        StartupWizard.use_of_funds.state: StartupWizard.use_of_funds,
        StartupWizard.geography.state: StartupWizard.geography,
        StartupWizard.founder_info.state: StartupWizard.founder_info,
        StartupWizard.contact_policy.state: StartupWizard.contact_policy,
        StartupWizard.preview.state: StartupWizard.preview,
        StartupWizard.premium_offer.state: StartupWizard.premium_offer,
        StartupWizard.edit_pick.state: StartupWizard.edit_pick,
    }.get(step)
    if state_obj is None:
        return
    await state.set_state(state_obj)
    catalog = CatalogRepository(session)

    if step == StartupWizard.name.state:
        await _ask(message, STARTUP_NAME)
    elif step == StartupWizard.pitch.state:
        await _ask(message, _pitch_step_prompt(startup))
    elif step == StartupWizard.description.state:
        await _ask(message, STARTUP_DESCRIPTION)
    elif step == StartupWizard.primary_sector.state:
        sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors()]
        await message.answer(STARTUP_PRIMARY, reply_markup=wizard_nav())
        await message.answer("Выберите направление:", reply_markup=single_choice_codes(sectors, CB_SEC))
    elif step == StartupWizard.secondary_sectors.state:
        primary = None
        if startup and startup.primary_sector_id:
            for s in await catalog.list_sectors():
                if s.id == startup.primary_sector_id:
                    primary = s.code
                    break
        sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors() if s.code != primary]
        data = await state.get_data()
        selected = set(data.get("secondary_sectors") or [])
        await message.answer(STARTUP_SECONDARY, reply_markup=wizard_nav())
        await message.answer(
            "Дополнительно:", reply_markup=sector_toggle_keyboard(sectors, selected)
        )
    elif step == StartupWizard.stage.state:
        stages = [(s.code, s.label) for s in await catalog.list_stages()]
        await message.answer(
            STARTUP_STAGE,
            reply_markup=single_choice_codes(stages, CB_STG, columns=2),
        )
    elif step == StartupWizard.links.state:
        await message.answer(STARTUP_LINKS, reply_markup=links_nav())
    elif step == StartupWizard.photos.state:
        svc = StartupService(session)
        count = 0
        cap = 3
        if startup is not None:
            count = await svc.photo_count(startup)
            cap = await svc.public_photo_cap(startup)
        if cap == 1:
            head = (
                "Пришлите одну фотографию или скриншот продукта "
                "(бесплатный лимит).\nStartup PRO открывает до 3 фото: /premium\n"
            )
        else:
            head = "Пришлите от 1 до 3 фотографий или скриншотов продукта.\n"
        lines = [head, "После загрузки нажмите «✅ Готово с фото»."]
        if count >= cap:
            lines.append(
                f"Сейчас сохранено {count}/{cap}. Замена фото в этом шаге "
                "пока недоступна. Оставьте текущие или нажмите «Готово»."
            )
        elif count > 0:
            lines.append(f"Сейчас сохранено {count}/{cap}.")
        await message.answer("\n".join(lines), reply_markup=photos_nav())
    elif step == StartupWizard.intents.state:
        data = await state.get_data()
        selected = set(data.get("intents") or (startup.intents if startup else []) or [])
        await state.update_data(intents=list(selected))
        await message.answer(STARTUP_INTENTS, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=intent_toggle_keyboard(selected))
    elif step == StartupWizard.audience.state:
        selected = set((startup.audience_codes if startup else None) or [])
        await state.update_data(audience_codes=list(selected))
        await message.answer(STARTUP_AUDIENCE, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=audience_toggle_keyboard(selected))
    elif step == StartupWizard.audience_note.state:
        await _ask(message, STARTUP_AUDIENCE_NOTE)
    elif step == StartupWizard.progress.state:
        selected = set((startup.progress_codes if startup else None) or [])
        await state.update_data(progress_codes=list(selected))
        await message.answer(STARTUP_PROGRESS, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=progress_toggle_keyboard(selected))
    elif step == StartupWizard.monetization.state:
        await message.answer(STARTUP_MONETIZATION, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=monetization_keyboard())
    elif step == StartupWizard.monetization_note.state:
        await _ask(message, STARTUP_MONETIZATION_NOTE)
    elif step == StartupWizard.investment_ask.state and startup is not None:
        await _go_investment_ask(message, state, startup)
    elif step == StartupWizard.fundraising.state:
        await message.answer(STARTUP_FUNDRAISING, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=fundraising_keyboard())
    elif step == StartupWizard.use_of_funds.state:
        selected = set((startup.use_of_funds_codes if startup else None) or [])
        await state.update_data(use_of_funds_codes=list(selected))
        await message.answer(STARTUP_USE_OF_FUNDS, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=use_of_funds_keyboard(selected))
    elif step == StartupWizard.geography.state:
        await message.answer(STARTUP_GEO, reply_markup=wizard_nav())
        await message.answer("Регион:", reply_markup=geography_keyboard())
    elif step == StartupWizard.founder_info.state:
        await _ask(message, STARTUP_FOUNDER)
    elif step == StartupWizard.contact_policy.state:
        await message.answer(STARTUP_CONTACT, reply_markup=contact_policy_keyboard())
    elif step == StartupWizard.preview.state and startup is not None:
        await _show_preview(message, session, startup)
    elif step == StartupWizard.edit_pick.state:
        await message.answer(STARTUP_EDIT_PICK, reply_markup=preview_nav())
        await message.answer("Поля:", reply_markup=edit_fields_keyboard())
    elif step == StartupWizard.premium_offer.state and startup is not None:
        await _show_startup_premium_offer(message, session, startup)
    elif step == StartupWizard.hub.state:
        await message.answer("Вернитесь в раздел через /startup.", reply_markup=main_menu())


async def _startup_stage_title(session: AsyncSession, startup) -> str:
    if startup.stage_id is None:
        return "не указана"
    for stage in await CatalogRepository(session).list_stages():
        if stage.id == startup.stage_id:
            return stage.label
    return "не указана"


async def _show_startup_manage(
    message: Message, session: AsyncSession, state: FSMContext, startup
) -> None:
    await state.set_state(StartupWizard.manage)
    await state.update_data(startup_id=str(startup.id))
    extra = ""
    if startup.review_state == REVIEW_PENDING_CHANGES:
        extra = (
            "\n\nНовая редакция ожидает отправки на проверку. "
            "Пока действует предыдущая одобренная версия."
        )
    text = (
        f"{STARTUP_MANAGE_HEADER}\n\n"
        f"Название: {escape(startup.name)}\n"
        f"Стадия: {await _startup_stage_title(session, startup)}\n"
        f"Статус анкеты: {review_state_label(startup.review_state)}"
        f"{extra}\n\n"
        "Управление анкетой"
    )
    await message.answer(text, reply_markup=manage_startup_keyboard())


async def _show_startup_hub(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    svc = StartupService(session)
    items = await svc.list_for_owner(db_user.id)
    archived = await svc.list_archived(db_user.id)
    limits = await limits_for_user(session, db_user.id)
    limit = limits.startup
    plan_note = (
        f"· Startup PRO до {_plan_date(limit.plan_until)}"
        if limit.plan_code
        else "· бесплатный тариф"
    )
    lines = ["📝 Мои проекты", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    if not items and not archived:
        lines.append(STARTUP_HUB_EMPTY)
    for index, startup in enumerate(items, start=1):
        lines.append(
            f"{index}. {escape(startup.name)} · {review_state_label(startup.review_state)}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{index}. {startup.name[:40]}",
                    callback_data=f"st:sel:{startup.id}",
                )
            ]
        )
    if archived:
        lines.append("")
        lines.append("Архив:")
        for startup in archived[:5]:
            lines.append(f"• {escape(startup.name)}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"↩ Восстановить: {startup.name[:30]}",
                        callback_data=f"st:res:{startup.id}",
                    )
                ]
            )
    lines.append("")
    lines.append(f"Стартапы: {limit.used} из {limit.limit} {plan_note}.")
    if limit.available > 0:
        buttons.append(
            [InlineKeyboardButton(text="➕ Новый стартап", callback_data="st:new")]
        )
    else:
        lines.append("Лимит достигнут: создать ещё один можно с подходящим Premium.")
    await state.set_state(StartupWizard.hub)
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def entry_startup(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user):
        return
    await _show_startup_hub(message, session, db_user, state)


async def on_startup_select(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup_id = (callback.data or "").rsplit(":", 1)[-1]
    startup = await StartupService(session).get_owned(uuid.UUID(startup_id), db_user.id)
    if startup is None:
        await callback.answer("Проект не найден.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(startup_id=str(startup.id))
    await _show_startup_manage(callback.message, session, state, startup)


async def on_startup_new_callback(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    try:
        await ensure_can_create(session, user_id=db_user.id, kind=KIND_STARTUP)
    except ProfileLimitError:
        limits = await limits_for_user(session, db_user.id)
        await callback.answer(
            f"Лимит: {limits.startup.used} из {limits.startup.limit}. "
            "Для ещё одного профиля нужен Startup PRO.",
            show_alert=True,
        )
        return
    await callback.answer()
    await state.set_state(StartupWizard.name)
    await state.update_data(startup_id=None, secondary_sectors=[], intents=[])
    await _ask(callback.message, STARTUP_NAME)


async def on_startup_restore(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup_id = (callback.data or "").rsplit(":", 1)[-1]
    try:
        startup = await StartupService(session).restore_owned(
            uuid.UUID(startup_id), db_user.id
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Проект восстановлен")
    await _show_startup_hub(callback.message, session, db_user, state)
    await _show_startup_manage(callback.message, session, state, startup)


async def hub_create(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    try:
        await ensure_can_create(session, user_id=db_user.id, kind=KIND_STARTUP)
    except ProfileLimitError:
        limits = await limits_for_user(session, db_user.id)
        await message.answer(
            "🚀 На бесплатном тарифе можно создать один стартап.\n\n"
            "Чтобы добавить ещё один проект, потребуется Startup PRO.\n"
            "Вы можете продолжать редактировать существующий стартап.\n\n"
            f"Мои стартапы: {limits.startup.used} из {limits.startup.limit}.",
            reply_markup=main_menu(),
        )
        return
    await state.set_state(StartupWizard.name)
    await state.update_data(startup_id=None, secondary_sectors=[], intents=[])
    await _ask(message, STARTUP_NAME)


async def hub_continue(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        items = await StartupService(session).list_for_owner(db_user.id)
        draft = next((s for s in items if can_edit_review_state(s.review_state)), None)
        if draft is None:
            if items:
                await _show_startup_manage(message, session, state, items[0])
            else:
                await message.answer("Проект не найден. Создайте новый.")
            return
        startup = draft
        await state.update_data(startup_id=str(draft.id))
    if startup.review_state not in {REVIEW_DRAFT, REVIEW_CHANGES, REVIEW_REJECTED}:
        await _show_startup_manage(message, session, state, startup)
        return
    photo_count = await StartupService(session).photo_count(startup)
    step = next_startup_step(startup, photo_count=photo_count)
    await state.update_data(
        secondary_sectors=[],
        intents=list(startup.intents or []),
    )
    await _prompt_startup_state(message, session, state, step, startup)


async def cancel_wizard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Анкета стартапа прервана.", reply_markup=main_menu())


async def save_draft(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    await session.commit()
    await message.answer(
        "Черновик сохранён."
        + (f" Статус: {review_state_label(startup.review_state)}." if startup else ""),
        reply_markup=main_menu(),
    )
    await state.clear()


async def on_back(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    current = await state.get_state()
    data = await state.get_data()
    startup = await _load_startup(session, state, db_user)
    if current == StartupWizard.edit_pick.state:
        await state.update_data(edit_mode=False)
        await state.set_state(StartupWizard.preview)
        if startup is not None:
            await _show_preview(message, session, startup)
        return
    if data.get("edit_mode"):
        await state.update_data(edit_mode=False)
        await state.set_state(StartupWizard.edit_pick)
        await message.answer(
            "Что изменить? Остальные поля сохранятся.",
            reply_markup=preview_nav(),
        )
        await message.answer("Поля:", reply_markup=edit_fields_keyboard())
        return
    prev = previous_startup_step(current)
    if prev is None:
        await message.answer("Некуда возвращаться.")
        return
    await _prompt_startup_state(message, session, state, prev, startup)


async def on_name(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    err = validate_name(message.text or "")
    if err:
        await message.answer(err)
        return
    svc = StartupService(session)
    data = await state.get_data()
    if data.get("startup_id"):
        startup = await _load_startup(session, state, db_user)
        if startup is None:
            await message.answer("Черновик недоступен.")
            return
        await svc.ensure_editable(startup)
        await _prepare_startup_write(session, startup)
        startup.name = message.text.strip()
    else:
        try:
            startup = await svc.create_draft(db_user.id, message.text.strip())
        except ProfileLimitError:
            await state.clear()
            await message.answer(
                "🚀 На бесплатном тарифе можно создать один стартап. "
                "Для второго потребуется Startup PRO.\n\n"
                "Существующий стартап можно редактировать как обычно.",
                reply_markup=main_menu(),
            )
            return
        await state.update_data(startup_id=str(startup.id))
        await AnalyticsService(session).track("startup_draft_started", user_id=db_user.id)

    async def _next() -> None:
        await state.set_state(StartupWizard.pitch)
        await _ask(message, PITCH_PROMPT)

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_pitch(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    err = validate_pitch(message.text or "", previous=startup.one_liner)
    if err:
        await message.answer(err)
        return
    await _prepare_startup_write(session, startup)
    startup.one_liner = message.text.strip()

    async def _next() -> None:
        await state.set_state(StartupWizard.description)
        await _ask(message, STARTUP_DESCRIPTION)

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_description(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    err = validate_description(
        message.text or "",
        previous=startup.full_description,
    )
    if err:
        await message.answer(err)
        return
    await _prepare_startup_write(session, startup)
    startup.full_description = message.text.strip()

    async def _next() -> None:
        await state.set_state(StartupWizard.primary_sector)
        catalog = CatalogRepository(session)
        sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors()]
        await message.answer(STARTUP_PRIMARY, reply_markup=wizard_nav())
        await message.answer("Выберите направление:", reply_markup=single_choice_codes(sectors, CB_SEC))

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_primary_sector_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_SEC) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    try:
        await StartupService(session).set_primary_sector(startup, code)
    except StartupServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await state.set_state(StartupWizard.secondary_sectors)
    await state.update_data(secondary_sectors=[])
    catalog = CatalogRepository(session)
    sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors() if s.code != code]
    await callback.answer()
    await callback.message.answer(
        "Дополнительные направления (необязательно). Отметьте и нажмите «Готово»:",
        reply_markup=sector_toggle_keyboard(sectors, set()),
    )


async def on_secondary_toggle(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_SEC) :]
    data = await state.get_data()
    selected = set(data.get("secondary_sectors") or [])
    if code in selected:
        selected.remove(code)
    else:
        if len(selected) >= 3:
            await callback.answer("Не больше 3 дополнительных.", show_alert=True)
            return
        selected.add(code)
    await state.update_data(secondary_sectors=list(selected))
    catalog = CatalogRepository(session)
    startup = await _load_startup(session, state, db_user)
    primary = None
    if startup and startup.primary_sector_id:
        for s in await catalog.list_sectors():
            if s.id == startup.primary_sector_id:
                primary = s.code
                break
    sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors() if s.code != primary]
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=sector_toggle_keyboard(sectors, selected)
        )


async def on_secondary_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = set(data.get("secondary_sectors") or [])
    if startup is None or callback.message is None:
        return
    try:
        await StartupService(session).set_secondary_sectors(startup, selected)
    except StartupServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return

    async def _next() -> None:
        await state.set_state(StartupWizard.stage)
        catalog = CatalogRepository(session)
        stages = [(s.code, s.label) for s in await catalog.list_stages()]
        await callback.answer()
        await callback.message.answer(
            "Выберите стадию:",
            reply_markup=single_choice_codes(stages, CB_STG),
        )

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_stage_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_STG) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    try:
        await StartupService(session).set_stage(startup, code)
    except StartupServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return

    async def _next() -> None:
        await state.set_state(StartupWizard.links)
        await callback.answer()
        await callback.message.answer(
            "Отправьте HTTPS-ссылку на сайт, приложение или демо.\n"
            "Или нажмите «Нет ссылки пока» и кратко объясните.",
            reply_markup=links_nav(),
        )

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_links(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    if (message.text or "") in {BTN_BACK, BTN_CANCEL, BTN_SAVE}:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    text = (message.text or "").strip()
    if text == BTN_NO_LINK:
        await state.update_data(awaiting_no_link_reason=True)
        await message.answer("Почему пока нет ссылки? (кратко)", reply_markup=wizard_nav())
        return
    data = await state.get_data()
    await _prepare_startup_write(session, startup)
    if data.get("awaiting_no_link_reason"):
        startup.no_link_reason = text[:240]
        startup.website_url = None
        await state.update_data(awaiting_no_link_reason=False)
    else:
        url = normalize_https_url(text)
        if url is None:
            await message.answer("Нужна корректная HTTPS-ссылка или «Нет ссылки пока».")
            return
        startup.website_url = url
        startup.no_link_reason = None

    async def _next() -> None:
        await state.set_state(StartupWizard.photos)
        svc = StartupService(session)
        cap = await svc.public_photo_cap(startup)
        if cap == 1:
            head = (
                "Пришлите одну фотографию или скриншот продукта "
                "(бесплатный лимит).\nStartup PRO открывает до 3 фото: /premium\n"
            )
        else:
            head = "Пришлите от 1 до 3 фотографий или скриншотов продукта.\n"
        await message.answer(
            head + "После загрузки нажмите «✅ Готово с фото».",
            reply_markup=photos_nav(),
        )

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_photo(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None or not message.photo:
        return
    photo = message.photo[-1]
    try:
        media = await StartupService(session).add_photo(
            startup,
            telegram_file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            width=photo.width,
            height=photo.height,
            size=photo.file_size,
        )
    except StartupServiceError as exc:
        await message.answer(exc.message)
        return
    count = await StartupService(session).photo_count(startup)
    cap = await StartupService(session).public_photo_cap(startup)
    await message.answer(f"Фото {media.position} сохранено ({count}/{cap}).")


async def on_photos_done(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    count = await StartupService(session).photo_count(startup)
    if count < 1:
        await message.answer("Нужно хотя бы одно фото.")
        return

    async def _next() -> None:
        await state.set_state(StartupWizard.intents)
        await state.update_data(intents=list(startup.intents or []))
        selected = set(startup.intents or [])
        await message.answer(
            "Что ищет проект? Отметьте варианты и нажмите «Готово»:",
            reply_markup=wizard_nav(),
        )
        await message.answer("Цели:", reply_markup=intent_toggle_keyboard(selected))

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_intent_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_INTENT) :]
    if code not in STARTUP_INTENT_LABELS:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("intents") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)
    await state.update_data(intents=list(selected))
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=intent_toggle_keyboard(selected))


async def on_intent_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = [c for c in (data.get("intents") or []) if c in STARTUP_INTENT_LABELS]
    if not selected:
        await callback.answer("Выберите хотя бы одну цель.", show_alert=True)
        return
    if startup is None or callback.message is None:
        return
    await _prepare_startup_write(session, startup)
    startup.intents = selected

    async def _next() -> None:
        await callback.answer()
        await state.set_state(StartupWizard.audience)
        selected_aud = set(startup.audience_codes or [])
        await state.update_data(audience_codes=list(selected_aud))
        await callback.message.answer(
            "Для кого продукт (целевая аудитория)? Отметьте и нажмите «Готово»:",
            reply_markup=wizard_nav(),
        )
        await callback.message.answer(
            "Аудитория:", reply_markup=audience_toggle_keyboard(selected_aud)
        )

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_audience_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_AUD) :]
    if code not in STARTUP_AUDIENCE_LABELS:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("audience_codes") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)
    await state.update_data(audience_codes=list(selected))
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=audience_toggle_keyboard(selected)
        )


async def on_audience_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = [c for c in (data.get("audience_codes") or []) if c in STARTUP_AUDIENCE_LABELS]
    if not selected:
        await callback.answer("Выберите хотя бы один вариант.", show_alert=True)
        return
    if startup is None or callback.message is None:
        return
    await _prepare_startup_write(session, startup)
    startup.audience_codes = selected
    if "other" in selected:
        await callback.answer()
        await state.set_state(StartupWizard.audience_note)
        await callback.message.answer(
            "Кратко уточните аудиторию (до 240 символов):",
            reply_markup=wizard_nav(),
        )
        return
    startup.audience_note = None

    async def _next() -> None:
        await callback.answer()
        await _go_progress(callback.message, session, state, startup)

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_audience_note(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    note = (message.text or "").strip()[:240]
    if len(note) < 2:
        await message.answer("Слишком коротко. Добавьте пару слов.")
        return
    await _prepare_startup_write(session, startup)
    startup.audience_note = note

    async def _next() -> None:
        await _go_progress(message, session, state, startup)

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def _go_progress(message: Message, session: AsyncSession, state: FSMContext, startup) -> None:
    await state.set_state(StartupWizard.progress)
    selected = set(startup.progress_codes or [])
    await state.update_data(progress_codes=list(selected))
    await message.answer(
        "Что уже подтверждено фактом? Не отмечайте планы.\n"
        "Можно несколько пунктов, затем «Готово»:",
        reply_markup=wizard_nav(),
    )
    await message.answer("Прогресс:", reply_markup=progress_toggle_keyboard(selected))


async def on_progress_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_PROG) :]
    if code not in STARTUP_PROGRESS_LABELS:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("progress_codes") or [])
    if code == "none_yet":
        selected = {"none_yet"} if "none_yet" not in selected else set()
    else:
        selected.discard("none_yet")
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
    await state.update_data(progress_codes=list(selected))
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=progress_toggle_keyboard(selected)
        )


async def on_progress_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = [c for c in (data.get("progress_codes") or []) if c in STARTUP_PROGRESS_LABELS]
    if not selected:
        await callback.answer("Отметьте хотя бы один факт.", show_alert=True)
        return
    if startup is None or callback.message is None:
        return
    await _prepare_startup_write(session, startup)
    startup.progress_codes = selected

    async def _next() -> None:
        await callback.answer()
        await state.set_state(StartupWizard.monetization)
        await callback.message.answer(STARTUP_MONETIZATION, reply_markup=wizard_nav())
        await callback.message.answer("Варианты:", reply_markup=monetization_keyboard())

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_monetization_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_MON) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    if code not in {"not_yet", "planned", "active"}:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    # Honesty: cannot claim revenue progress without active monetization status,
    # and cannot claim active monetization without revenue progress signal.
    progress = set(startup.progress_codes or [])
    if code == "active" and "revenue" not in progress:
        await callback.answer(
            "Для «Уже зарабатываем» отметьте «Подтверждённая выручка» в прогрессе.",
            show_alert=True,
        )
        return
    if code != "active" and "revenue" in progress:
        await callback.answer(
            "В прогрессе указана выручка. Выберите «Уже зарабатываем» "
            "или уберите выручку из прогресса.",
            show_alert=True,
        )
        return
    await _prepare_startup_write(session, startup)
    startup.monetization_status = code
    if code in {"planned", "active"}:
        await callback.answer()
        await state.set_state(StartupWizard.monetization_note)
        await callback.message.answer(STARTUP_MONETIZATION_NOTE, reply_markup=wizard_nav())
        return
    startup.monetization_note = None

    async def _next() -> None:
        await callback.answer()
        await _go_investment_ask(callback.message, state, startup)

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_monetization_note(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    note = (message.text or "").strip()[:240]
    if len(note) < 3:
        await message.answer("Добавьте короткое уточнение.")
        return
    await _prepare_startup_write(session, startup)
    startup.monetization_note = note

    async def _next() -> None:
        await _go_investment_ask(message, state, startup)

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def _go_investment_ask(message: Message, state: FSMContext, startup) -> None:
    await state.set_state(StartupWizard.investment_ask)
    current = format_ask_amount(startup.ask_amount_minor, startup.ask_currency)
    has_ask = bool(startup.ask_amount_minor)
    lines = [STARTUP_ASK]
    if current:
        lines.append(f"Сейчас сохранено: {current}.")
        lines.append("«Пропустить» оставит текущую сумму. «Убрать сумму» удалит запрос.")
    await message.answer(
        "\n".join(lines),
        reply_markup=wizard_nav(include_skip=True, include_clear_ask=has_ask),
    )


async def on_investment_ask(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in {BTN_BACK, BTN_CANCEL, BTN_SAVE}:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None:
        return
    text = (message.text or "").strip()
    resolved = apply_investment_ask_step(
        text,
        current_minor=startup.ask_amount_minor,
        current_currency=startup.ask_currency,
        clear_label=BTN_CLEAR_ASK,
        skip_label=BTN_SKIP,
    )
    if resolved is None:
        await message.answer(
            "Не понял сумму. Пример: 15000000 RUB. "
            "Или «Пропустить» / «Убрать сумму»."
        )
        return
    await _prepare_startup_write(session, startup)
    startup.ask_amount_minor, startup.ask_currency = resolved

    async def _next() -> None:
        await state.set_state(StartupWizard.fundraising)
        hint = ""
        if startup.ask_amount_minor:
            hint = (
                "\nУ вас указан инвестиционный запрос. Выберите стадию раунда, "
                "не вариант «Сейчас не привлекаем»."
            )
        await message.answer(
            f"{STARTUP_FUNDRAISING}{hint}",
            reply_markup=wizard_nav(),
        )
        await message.answer("Варианты:", reply_markup=fundraising_keyboard())

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_fundraising_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_FUND) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    from raundo.domain import STARTUP_FUNDRAISING_LABELS

    if code not in STARTUP_FUNDRAISING_LABELS:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    if code == "not_raising" and startup.ask_amount_minor:
        await callback.answer(
            "Сначала уберите инвестиционный запрос («Убрать сумму») "
            "или выберите стадию раунда.",
            show_alert=True,
        )
        return
    await _prepare_startup_write(session, startup)
    startup.fundraising_stage = code
    raising = code != "not_raising" or bool(startup.ask_amount_minor)
    if raising:
        await callback.answer()
        await state.set_state(StartupWizard.use_of_funds)
        selected = set(startup.use_of_funds_codes or [])
        await state.update_data(use_of_funds_codes=list(selected))
        await callback.message.answer(
            "На что пойдут средства? Отметьте и нажмите «Готово»:",
            reply_markup=wizard_nav(),
        )
        await callback.message.answer(
            "Использование:", reply_markup=use_of_funds_keyboard(selected)
        )
        return
    startup.use_of_funds_codes = []
    startup.use_of_funds_note = None

    async def _next() -> None:
        await callback.answer()
        await _go_geography(callback.message, state)

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_use_of_funds_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_UOF) :]
    if code not in STARTUP_USE_OF_FUNDS_LABELS:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("use_of_funds_codes") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)
    await state.update_data(use_of_funds_codes=list(selected))
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=use_of_funds_keyboard(selected)
        )


async def on_use_of_funds_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = [
        c for c in (data.get("use_of_funds_codes") or []) if c in STARTUP_USE_OF_FUNDS_LABELS
    ]
    if not selected:
        await callback.answer("Выберите хотя бы один пункт.", show_alert=True)
        return
    if startup is None or callback.message is None:
        return
    await _prepare_startup_write(session, startup)
    startup.use_of_funds_codes = selected

    async def _next() -> None:
        await callback.answer()
        await _go_geography(callback.message, state)

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def _go_geography(message: Message, state: FSMContext) -> None:
    await state.set_state(StartupWizard.geography)
    await message.answer(
        "🌍 География проекта\n\nВыберите регион:", reply_markup=geography_keyboard()
    )


async def on_startup_geo_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_GEO) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    mapping = {"ru": "Россия", "world": "Международный"}
    if code not in mapping:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    await _prepare_startup_write(session, startup)
    startup.country = mapping[code]

    async def _next() -> None:
        await callback.answer()
        await state.set_state(StartupWizard.founder_info)
        await callback.message.answer(
            "Имя представителя и роль через «|».\nПример: Иван Петров | CEO",
            reply_markup=wizard_nav(),
        )

    await _after_edit_or_continue_cb(
        callback, session, db_user, state, continue_fn=_next
    )


async def on_founder_info(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    startup = await _editable_for_message(message, session, state, db_user)
    if startup is None or not message.text:
        return
    if "|" not in message.text:
        await message.answer("Укажите «Имя | Роль».")
        return
    name, role = [p.strip() for p in message.text.split("|", 1)]
    if len(name) < 2 or len(role) < 2:
        await message.answer("Имя и роль слишком короткие.")
        return
    await _prepare_startup_write(session, startup)
    startup.founder_display_name = name[:128]
    startup.founder_role = role[:128]

    async def _next() -> None:
        await state.set_state(StartupWizard.contact_policy)
        await message.answer(
            "Как можно связаться после принятого запроса знакомства?\n"
            "❤️ лайк сам по себе контакт не раскрывает.",
            reply_markup=contact_policy_keyboard(),
        )

    await _after_edit_or_continue(message, session, db_user, state, continue_fn=_next)


async def on_contact_policy(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    policy = (callback.data or "")[len(CB_CONTACT) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    await _prepare_startup_write(session, startup)
    startup.contact_policy = policy if policy in {"relay", "username"} else "relay"
    startup.show_contact_after_accept = policy == "username"
    if policy == "username" and db_user.username:
        startup.contact_username = db_user.username
    svc = StartupService(session)
    if startup.review_state == REVIEW_PENDING_CHANGES and policy != "username":
        from raundo.services.profile_lifecycle import withdraw_startup_sensitive_public

        await withdraw_startup_sensitive_public(session, startup)
    await svc.register_owner_change(startup)
    await state.update_data(edit_mode=False)
    await state.set_state(StartupWizard.preview)
    await callback.answer()
    await _show_preview(callback.message, session, startup)


async def _resolve_card_labels(
    session: AsyncSession, startup
) -> tuple[list[str], str | None]:
    catalog = CatalogRepository(session)
    sectors = await catalog.list_sectors()
    by_id = {s.id: s.name_ru for s in sectors}
    labels: list[str] = []
    primary_name: str | None = None
    if startup.primary_sector_id and startup.primary_sector_id in by_id:
        primary_name = by_id[startup.primary_sector_id]
        labels.append(primary_name)
    for link in startup.sectors or []:
        name = by_id.get(link.sector_id)
        if name and name not in labels:
            labels.append(name)
    stage_label = None
    if startup.stage_id:
        for stage in await catalog.list_stages():
            if stage.id == startup.stage_id:
                stage_label = stage.label
                break
    return labels, stage_label


async def _show_preview(message: Message, session: AsyncSession, startup) -> None:
    """One compact card: cover + HTML caption (keyboard unchanged)."""
    sector_labels, stage_label = await _resolve_card_labels(session, startup)
    view = build_startup_card_view(
        startup,
        sector_labels=sector_labels,
        stage_label=stage_label,
        include_owner_private=True,
    )
    caption = compact_startup_html(view, owner_preview=True)
    nav = preview_nav()
    if view.cover_file_id and message.bot is not None:
        photo = await resolve_card_photo(message.bot, view)
        await message.answer_photo(
            photo=photo,
            caption=caption,
            parse_mode="HTML",
            reply_markup=nav,
        )
    elif view.cover_file_id:
        await message.answer_photo(
            photo=view.cover_file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=nav,
        )
    else:
        await message.answer(caption, parse_mode="HTML", reply_markup=nav)


async def _send_gallery(message: Message, file_ids: tuple[str, ...]) -> None:
    """Native Telegram media group for 2–3 photos. One photo → no extra message."""
    if len(file_ids) < 2 or message.bot is None:
        return
    media = [InputMediaPhoto(media=fid) for fid in file_ids]
    try:
        await message.answer_media_group(media=media)
        return
    except Exception:
        pass
    sent = 0
    for fid in file_ids:
        try:
            await message.answer_photo(photo=fid)
            sent += 1
        except Exception:
            continue
    if sent == 0:
        await message.answer(
            "Не удалось показать галерею фото. Текст карточки выше сохранён. "
            "Попробуйте «Подробнее» позже."
        )


async def _show_details(message: Message, session: AsyncSession, startup) -> None:
    sector_labels, stage_label = await _resolve_card_labels(session, startup)
    view = build_startup_card_view(
        startup,
        sector_labels=sector_labels,
        stage_label=stage_label,
        include_owner_private=True,
    )
    await message.answer(
        details_startup_html(view, owner_preview=True),
        parse_mode="HTML",
        reply_markup=preview_nav(),
    )
    await _send_gallery(message, view.gallery_file_ids)


async def on_edit_field_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_EDIT) :]
    startup = await _editable_for_callback(callback, session, state, db_user)
    if startup is None or callback.message is None:
        return
    if code == "back":
        await state.update_data(edit_mode=False)
        await state.set_state(StartupWizard.preview)
        await callback.answer()
        await _show_preview(callback.message, session, startup)
        return

    field_to_step = {
        "name": StartupWizard.name.state,
        "pitch": StartupWizard.pitch.state,
        "description": StartupWizard.description.state,
        "sectors": StartupWizard.primary_sector.state,
        "stage": StartupWizard.stage.state,
        "links": StartupWizard.links.state,
        "photos": StartupWizard.photos.state,
        "intents": StartupWizard.intents.state,
        "audience": StartupWizard.audience.state,
        "progress": StartupWizard.progress.state,
        "monetization": StartupWizard.monetization.state,
        "ask": StartupWizard.investment_ask.state,
        "fundraising": StartupWizard.fundraising.state,
        "geography": StartupWizard.geography.state,
        "founder": StartupWizard.founder_info.state,
        "contact": StartupWizard.contact_policy.state,
    }
    step = field_to_step.get(code)
    if step is None:
        await callback.answer("Неизвестное поле.", show_alert=True)
        return
    await state.update_data(edit_mode=True)
    await callback.answer()
    await _prompt_startup_state(callback.message, session, state, step, startup)


async def on_preview(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        return
    text = message.text or ""
    if text == BTN_MORE:
        await _show_details(message, session, startup)
        return
    if text == BTN_EDIT:
        if startup.review_state in {
            REVIEW_APPROVED,
            REVIEW_SUBMITTED,
            REVIEW_UNDER_REVIEW,
            REVIEW_PENDING_CHANGES,
        }:
            await message.answer(REEDIT_MODERATION_NOTICE)
        await state.set_state(StartupWizard.edit_pick)
        await state.update_data(edit_mode=False)
        await message.answer(
            "Что изменить? Остальные поля сохранятся.",
            reply_markup=preview_nav(),
        )
        await message.answer("Поля:", reply_markup=edit_fields_keyboard())
        return
    if text == BTN_SAVE:
        await save_draft(message, session, db_user, state)
        return
    if text != BTN_SUBMIT:
        await message.answer("Выберите действие на клавиатуре.")
        return
    if await PremiumService(session).has_active_startup_pro(startup.id):
        await _commit_startup_submit(message, session, db_user, state, startup)
        return
    await _show_startup_premium_offer(message, session, startup)
    await state.set_state(StartupWizard.premium_offer)


async def _show_startup_premium_offer(message: Message, session: AsyncSession, startup) -> None:
    active = await PremiumService(session).has_active_startup_pro(startup.id)
    if active:
        text = PREMIUM_ALREADY_ACTIVE.format(title=PLAN_TITLES[PLAN_STARTUP_PRO])
    else:
        text = PREMIUM_OFFER_STARTUP
    await message.answer(text, reply_markup=premium_offer_nav())


async def _commit_startup_submit(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext, startup
) -> None:
    await state.update_data(
        pending_submit_kind="startup", pending_submit_id=str(startup.id)
    )
    if not await has_publication_consent(session, db_user.id):
        await message.answer(
            PUBLICATION_CONSENT_TEXT, reply_markup=publication_consent_keyboard()
        )
        return
    try:
        await StartupService(session).submit_for_review(startup, db_user.id)
        await AnalyticsService(session).track("startup_submitted", user_id=db_user.id)
        await NotificationService(session).enqueue(
            kind="startup.submitted",
            dedupe_key=f"startup_submitted:{startup.id}:{startup.current_revision}",
            recipient_user_id=db_user.id,
            resource_type="startup",
            resource_id=str(startup.id),
            safe_payload={"review_state": REVIEW_SUBMITTED},
        )
        await session.commit()
    except StartupServiceError as exc:
        await session.rollback()
        await message.answer(exc.message)
        return
    except Exception:
        await session.rollback()
        await message.answer("Не удалось отправить анкету. Попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer(
        SUBMITTED_STARTUP.format(name=startup.name),
        reply_markup=main_menu(),
    )


async def on_startup_manage(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        await message.answer("Проект не найден.")
        return
    text = message.text or ""
    if text == BTN_CANCEL:
        await state.clear()
        await message.answer("Вы в главном меню.", reply_markup=main_menu())
        return
    if text == BTN_VIEW_CARD:
        await state.set_state(StartupWizard.preview)
        await _show_preview(message, session, startup)
        return
    if text == BTN_EDIT:
        if startup.review_state in {
            REVIEW_APPROVED,
            REVIEW_SUBMITTED,
            REVIEW_UNDER_REVIEW,
            REVIEW_PENDING_CHANGES,
        }:
            await message.answer(REEDIT_MODERATION_NOTICE)
        await state.set_state(StartupWizard.edit_pick)
        await message.answer(STARTUP_EDIT_PICK, reply_markup=preview_nav())
        await message.answer("Поля:", reply_markup=edit_fields_keyboard())
        return
    if text == BTN_DELETE_PROFILE:
        await state.set_state(StartupWizard.delete_confirm)
        await message.answer(DELETE_PROFILE_PROMPT, reply_markup=delete_confirm_keyboard())
        return
    await message.answer("Выберите действие на клавиатуре.", reply_markup=manage_startup_keyboard())


async def on_startup_delete_confirm(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        await state.clear()
        await message.answer("Проект не найден.", reply_markup=main_menu())
        return
    text = message.text or ""
    if text == BTN_CANCEL_DELETE:
        await _show_startup_manage(message, session, state, startup)
        return
    if text != BTN_CONFIRM_DELETE:
        await message.answer("Подтвердите или отмените удаление.", reply_markup=delete_confirm_keyboard())
        return
    svc = StartupService(session)
    name = startup.name
    try:
        await svc.delete_owned(startup, db_user.id)
        await session.commit()
    except StartupServiceError as exc:
        await session.rollback()
        await message.answer(exc.message)
        return
    await state.clear()
    await message.answer(DELETED_STARTUP.format(name=name), reply_markup=main_menu())


async def on_startup_premium_offer(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _load_startup(session, state, db_user)
    if startup is None:
        return
    text = message.text or ""
    if text == BTN_BACK_TO_FORM:
        await state.set_state(StartupWizard.preview)
        await _show_preview(message, session, startup)
        return
    if text == BTN_PREMIUM_VIEW:
        await message.answer(
            PREMIUM_INTRO,
            reply_markup=premium_root_keyboard(dev_grants_available=False),
        )
        await message.answer(plan_features_text(PLAN_STARTUP_PRO), reply_markup=premium_offer_nav())
        return
    if text == BTN_CONTINUE_FREE:
        await _commit_startup_submit(message, session, db_user, state, startup)
        return
    await message.answer("Выберите действие на клавиатуре.", reply_markup=premium_offer_nav())


def create_router() -> Router:
    router = Router(name="startup")
    router.message.register(entry_startup, Command("startup"))
    router.message.register(entry_startup, F.text == MENU_STARTUP)
    router.callback_query.register(on_startup_select, F.data.startswith("st:sel:"))
    router.callback_query.register(on_startup_new_callback, F.data == "st:new")
    router.callback_query.register(on_startup_restore, F.data.startswith("st:res:"))
    router.message.register(on_startup_manage, StartupWizard.manage, F.text)
    router.message.register(on_startup_delete_confirm, StartupWizard.delete_confirm, F.text)
    router.message.register(hub_create, StartupWizard.hub, F.text == BTN_CREATE_STARTUP)
    router.message.register(hub_continue, StartupWizard.hub, F.text == BTN_CONTINUE)
    router.message.register(cancel_wizard, StateFilter(StartupWizard), F.text == BTN_CANCEL)
    router.message.register(save_draft, StateFilter(StartupWizard), F.text == BTN_SAVE)
    router.message.register(on_back, StateFilter(StartupWizard), F.text == BTN_BACK)

    router.message.register(on_name, StartupWizard.name, F.text)
    router.message.register(on_pitch, StartupWizard.pitch, F.text)
    router.message.register(on_description, StartupWizard.description, F.text)
    router.callback_query.register(
        on_primary_sector_cb, StartupWizard.primary_sector, F.data.startswith(CB_SEC)
    )
    router.callback_query.register(
        on_secondary_toggle, StartupWizard.secondary_sectors, F.data.startswith(CB_SEC)
    )
    router.callback_query.register(
        on_secondary_done, StartupWizard.secondary_sectors, F.data == CB_DONE_SEC
    )
    router.callback_query.register(
        on_stage_cb, StartupWizard.stage, F.data.startswith(CB_STG)
    )
    router.message.register(on_links, StartupWizard.links, F.text)
    router.message.register(on_photo, StartupWizard.photos, F.photo)
    router.message.register(on_photos_done, StartupWizard.photos, F.text == BTN_PHOTOS_DONE)
    router.callback_query.register(
        on_intent_toggle, StartupWizard.intents, F.data.startswith(CB_INTENT)
    )
    router.callback_query.register(
        on_intent_done, StartupWizard.intents, F.data == CB_DONE_INT
    )
    router.callback_query.register(
        on_audience_toggle, StartupWizard.audience, F.data.startswith(CB_AUD)
    )
    router.callback_query.register(
        on_audience_done, StartupWizard.audience, F.data == CB_DONE_AUD
    )
    router.message.register(on_audience_note, StartupWizard.audience_note, F.text)
    router.callback_query.register(
        on_progress_toggle, StartupWizard.progress, F.data.startswith(CB_PROG)
    )
    router.callback_query.register(
        on_progress_done, StartupWizard.progress, F.data == CB_DONE_PROG
    )
    router.callback_query.register(
        on_monetization_cb, StartupWizard.monetization, F.data.startswith(CB_MON)
    )
    router.message.register(
        on_monetization_note, StartupWizard.monetization_note, F.text
    )
    router.message.register(on_investment_ask, StartupWizard.investment_ask, F.text)
    router.callback_query.register(
        on_fundraising_cb, StartupWizard.fundraising, F.data.startswith(CB_FUND)
    )
    router.callback_query.register(
        on_use_of_funds_toggle, StartupWizard.use_of_funds, F.data.startswith(CB_UOF)
    )
    router.callback_query.register(
        on_use_of_funds_done, StartupWizard.use_of_funds, F.data == CB_DONE_UOF
    )
    router.callback_query.register(
        on_startup_geo_cb, StartupWizard.geography, F.data.startswith(CB_GEO)
    )
    router.message.register(on_founder_info, StartupWizard.founder_info, F.text)
    router.callback_query.register(
        on_contact_policy, StartupWizard.contact_policy, F.data.startswith(CB_CONTACT)
    )
    router.callback_query.register(
        on_edit_field_cb, StartupWizard.edit_pick, F.data.startswith(CB_EDIT)
    )
    router.message.register(on_preview, StartupWizard.preview, F.text)
    router.message.register(on_preview, StartupWizard.edit_pick, F.text)
    router.message.register(on_startup_premium_offer, StartupWizard.premium_offer, F.text)
    return router
