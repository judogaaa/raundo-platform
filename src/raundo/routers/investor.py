"""Investor / funding participant wizard — type-aware form, draft/submit."""

from __future__ import annotations

import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.cards.investor_card import (
    build_investor_card_view,
    compact_investor_html,
    details_investor_html,
)
from raundo.config import get_settings  # noqa: F401 - tests patch this module attribute
from raundo.domain import (
    REVIEW_APPROVED,
    REVIEW_CHANGES,
    REVIEW_DRAFT,
    REVIEW_PENDING_CHANGES,
    REVIEW_REJECTED,
    REVIEW_SUBMITTED,
    REVIEW_UNDER_REVIEW,
    SUPPORT_MODE_LABELS,
    can_edit_review_state,
    cheque_is_relevant,
    investor_needs_organization,
    is_known_participant_type,
    normalize_https_url,
    parse_cheque_range_major,
    validate_name,
    validate_strategy,
)
from raundo.keyboards import adult_confirm_keyboard, main_menu, publication_consent_keyboard
from raundo.models.user import User
from raundo.premium_catalog import PLAN_INVESTOR_PRO, PLAN_ORGANIZATION_PRO, PLAN_TITLES
from raundo.premium_texts import PREMIUM_INTRO, plan_features_text
from raundo.repositories.catalog import CatalogRepository
from raundo.routers.premium import premium_root_keyboard
from raundo.routers.publication_text import PUBLICATION_CONSENT_TEXT
from raundo.services.age import ADULT_REQUIRED_MESSAGE, is_adult
from raundo.services.analytics import AnalyticsService
from raundo.services.consents import ConsentService, has_publication_consent
from raundo.services.investors import InvestorService, InvestorServiceError
from raundo.services.notifications import NotificationService
from raundo.services.organizations import OrganizationService, OrganizationServiceError
from raundo.services.premium import PremiumService
from raundo.services.profile_limits import (
    KIND_INVESTOR,
    ProfileLimitError,
    ensure_can_create,
    limits_for_user,
)
from raundo.state.investor import InvestorWizard
from raundo.texts import NEED_CONSENT
from raundo.ui_labels import review_state_label
from raundo.wizard_copy import (
    DELETE_PROFILE_PROMPT,
    DELETED_INVESTOR,
    INVESTOR_CHECK,
    INVESTOR_CONTACT,
    INVESTOR_GEO,
    INVESTOR_HUB_EMPTY,
    INVESTOR_LINK,
    INVESTOR_MANAGE_HEADER,
    INVESTOR_NAME,
    INVESTOR_ORG,
    INVESTOR_PHOTO,
    INVESTOR_POSITION,
    INVESTOR_SECTORS,
    INVESTOR_STAGES,
    INVESTOR_STRATEGY,
    INVESTOR_SUPPORT,
    INVESTOR_TYPE,
    PREMIUM_ALREADY_ACTIVE,
    PREMIUM_OFFER_INVESTOR,
    PREMIUM_OFFER_ORG,
    REEDIT_MODERATION_NOTICE,
    SUBMITTED_INVESTOR,
)
from raundo.wizard_flow import next_investor_step, previous_investor_step
from raundo.wizard_keyboards import (
    BTN_BACK,
    BTN_BACK_TO_FORM,
    BTN_CANCEL,
    BTN_CANCEL_DELETE,
    BTN_CHECK_INDIVIDUAL,
    BTN_CONFIRM_DELETE,
    BTN_CONTINUE,
    BTN_CONTINUE_FREE,
    BTN_CREATE_INVESTOR,
    BTN_DELETE_PROFILE,
    BTN_EDIT,
    BTN_MORE,
    BTN_PHOTOS_DONE,
    BTN_PREMIUM_VIEW,
    BTN_SAVE,
    BTN_SKIP,
    BTN_SUBMIT,
    BTN_VIEW_CARD,
    CB_CONTACT,
    CB_DONE_SEC,
    CB_DONE_SM,
    CB_DONE_STG,
    CB_GEO,
    CB_IEDT,
    CB_ITYPE,
    CB_SEC,
    CB_SMOC,
    CB_STG,
    check_nav,
    contact_policy_keyboard,
    delete_confirm_keyboard,
    geography_keyboard,
    hub_investor_keyboard,
    investor_edit_fields_keyboard,
    investor_type_keyboard,
    manage_investor_keyboard,
    photos_nav,
    premium_offer_nav,
    preview_nav,
    sector_toggle_keyboard,
    stage_toggle_keyboard,
    support_mode_keyboard,
    wizard_nav,
)

MENU_INVESTOR = "💼 Инвесторам"


def _plan_date(value: str | None) -> str:
    """ISO datetime/date from limits → DD.MM.YYYY without time or tz noise."""
    raw = (value or "").strip()
    if len(raw) < 10:
        return "дата не указана"
    year, month, day = raw[:10].split("-")
    return f"{day}.{month}.{year}"

_NAV_BUTTONS = frozenset(
    {BTN_BACK, BTN_CANCEL, BTN_SAVE, BTN_CHECK_INDIVIDUAL, BTN_SKIP, BTN_PHOTOS_DONE}
)


async def _require_consent(message: Message, session: AsyncSession, db_user: User) -> bool:
    ok = await ConsentService(session).has_required_consents(
        db_user.id
    )
    if not ok:
        await message.answer(NEED_CONSENT)
    return ok


async def _load(session: AsyncSession, state: FSMContext, db_user: User):
    data = await state.get_data()
    pid = data.get("investor_id")
    if not pid:
        return await InvestorService(session).get_for_user(db_user.id)
    return await InvestorService(session).get_owned(uuid.UUID(pid), db_user.id)


async def _load_editable(session: AsyncSession, state: FSMContext, db_user: User):
    profile = await _load(session, state, db_user)
    if profile is None:
        return None
    await InvestorService(session).ensure_editable(profile)
    return profile


async def _editable_for_message(
    message: Message, session: AsyncSession, state: FSMContext, db_user: User
):
    try:
        return await _load_editable(session, state, db_user)
    except InvestorServiceError as exc:
        await message.answer(exc.message)
        return None


async def _editable_for_callback(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, db_user: User
):
    try:
        profile = await _load_editable(session, state, db_user)
    except InvestorServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return None
    if profile is None:
        await callback.answer()
        return None
    return profile


async def _prepare_investor_write(session: AsyncSession, profile) -> None:
    if profile.review_status == REVIEW_APPROVED:
        await InvestorService(session).prepare_owner_field_write(profile)


async def _prompt_investor_state(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    step: str,
    profile=None,
) -> None:
    state_obj = {
        InvestorWizard.hub.state: InvestorWizard.hub,
        InvestorWizard.display_name.state: InvestorWizard.display_name,
        InvestorWizard.investor_type.state: InvestorWizard.investor_type,
        InvestorWizard.support_modes.state: InvestorWizard.support_modes,
        InvestorWizard.organization.state: InvestorWizard.organization,
        InvestorWizard.position.state: InvestorWizard.position,
        InvestorWizard.strategy.state: InvestorWizard.strategy,
        InvestorWizard.sectors.state: InvestorWizard.sectors,
        InvestorWizard.stages.state: InvestorWizard.stages,
        InvestorWizard.check_size.state: InvestorWizard.check_size,
        InvestorWizard.geography.state: InvestorWizard.geography,
        InvestorWizard.public_link.state: InvestorWizard.public_link,
        InvestorWizard.photo.state: InvestorWizard.photo,
        InvestorWizard.contact_policy.state: InvestorWizard.contact_policy,
        InvestorWizard.preview.state: InvestorWizard.preview,
        InvestorWizard.premium_offer.state: InvestorWizard.premium_offer,
        InvestorWizard.edit_pick.state: InvestorWizard.edit_pick,
    }.get(step)
    if state_obj is None:
        return
    await state.set_state(state_obj)
    catalog = CatalogRepository(session)

    if step == InvestorWizard.display_name.state:
        await message.answer(INVESTOR_NAME, reply_markup=wizard_nav())
    elif step == InvestorWizard.investor_type.state:
        await message.answer(INVESTOR_TYPE, reply_markup=investor_type_keyboard())
    elif step == InvestorWizard.support_modes.state:
        data = await state.get_data()
        selected = set(data.get("support_modes") or (profile.support_modes if profile else []) or [])
        await state.update_data(support_modes=list(selected))
        await message.answer(INVESTOR_SUPPORT, reply_markup=wizard_nav())
        await message.answer("Варианты:", reply_markup=support_mode_keyboard(selected))
    elif step == InvestorWizard.organization.state:
        await message.answer(INVESTOR_ORG, reply_markup=wizard_nav())
    elif step == InvestorWizard.position.state:
        await message.answer(INVESTOR_POSITION, reply_markup=wizard_nav())
    elif step == InvestorWizard.strategy.state:
        await message.answer(INVESTOR_STRATEGY, reply_markup=wizard_nav())
    elif step == InvestorWizard.sectors.state:
        sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors()]
        data = await state.get_data()
        selected = set(data.get("sectors") or [])
        await message.answer(INVESTOR_SECTORS, reply_markup=wizard_nav())
        await message.answer("Отрасли:", reply_markup=sector_toggle_keyboard(sectors, selected))
    elif step == InvestorWizard.stages.state:
        stages = [(s.code, s.label) for s in await catalog.list_stages()]
        data = await state.get_data()
        selected = set(data.get("stages") or [])
        await message.answer(INVESTOR_STAGES, reply_markup=wizard_nav())
        await message.answer("Стадии:", reply_markup=stage_toggle_keyboard(stages, selected))
    elif step == InvestorWizard.check_size.state:
        await message.answer(INVESTOR_CHECK, reply_markup=check_nav())
    elif step == InvestorWizard.geography.state:
        await message.answer(INVESTOR_GEO, reply_markup=geography_keyboard())
    elif step == InvestorWizard.public_link.state:
        await message.answer(INVESTOR_LINK, reply_markup=wizard_nav())
    elif step == InvestorWizard.photo.state:
        await message.answer(INVESTOR_PHOTO, reply_markup=photos_nav(include_skip=True))
    elif step == InvestorWizard.contact_policy.state:
        await message.answer(INVESTOR_CONTACT, reply_markup=contact_policy_keyboard())
    elif step == InvestorWizard.preview.state and profile is not None:
        await _show_preview(message, session, profile)
    elif step == InvestorWizard.premium_offer.state and profile is not None:
        await _show_investor_premium_offer(message, session, profile)
    elif step == InvestorWizard.edit_pick.state:
        await message.answer("Что изменить? Остальные поля сохранятся.", reply_markup=preview_nav())
        await message.answer("Поля:", reply_markup=investor_edit_fields_keyboard())
    elif step == InvestorWizard.hub.state:
        await message.answer("Вернитесь в раздел через /investor.", reply_markup=main_menu())


async def _show_investor_manage(
    message: Message, session: AsyncSession, state: FSMContext, profile
) -> None:
    await state.set_state(InvestorWizard.manage)
    await state.update_data(investor_id=str(profile.id))
    extra = ""
    if profile.review_status == REVIEW_PENDING_CHANGES:
        extra = (
            "\n\nНовая редакция ожидает отправки на проверку. "
            "Пока действует предыдущая одобренная версия."
        )
    body = (
        f"{INVESTOR_MANAGE_HEADER}\n\n"
        f"Имя: {escape(profile.display_name or 'не указано')}\n"
        f"Статус анкеты: {review_state_label(profile.review_status)}"
        f"{extra}\n\n"
        "Управление анкетой"
    )
    await message.answer(body, reply_markup=manage_investor_keyboard())


async def _show_investor_hub(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    from raundo.models.organization import FundingOrganization

    service = InvestorService(session)
    profiles = await service.list_for_user(db_user.id)
    archived = await service.list_archived(db_user.id)
    limits = await limits_for_user(session, db_user.id)
    limit = limits.investor
    orgs = list(
        (
            await session.execute(
                select(FundingOrganization)
                .where(FundingOrganization.created_by_user_id == db_user.id)
                .order_by(FundingOrganization.created_at)
            )
        ).scalars()
    )
    plan_note = (
        f"· Investor PRO до {_plan_date(limit.plan_until)}"
        if limit.plan_code
        else "· бесплатный тариф"
    )
    lines = ["💼 Мои инвесторские профили", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    if not profiles and not archived:
        lines.append(INVESTOR_HUB_EMPTY)
    for index, profile in enumerate(profiles, start=1):
        name = profile.display_name or f"Профиль {index}"
        lines.append(f"{index}. {escape(name)} · {review_state_label(profile.review_status)}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{index}. {name[:40]}",
                    callback_data=f"inv:sel:{profile.id}",
                )
            ]
        )
    if archived:
        lines.append("")
        lines.append("Архив:")
        for profile in archived[:5]:
            name = profile.display_name or "Профиль"
            lines.append(f"• {escape(name)}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"↩ Восстановить: {name[:30]}",
                        callback_data=f"inv:res:{profile.id}",
                    )
                ]
            )
    lines.append("")
    lines.append(f"Профили инвестора: {limit.used} из {limit.limit} {plan_note}.")
    lines.append(
        f"Собственные организации: {limits.organization.used} из {limits.organization.limit}."
    )
    if orgs:
        for org in orgs:
            lines.append(f"• {escape(org.name)} · {review_state_label(org.review_status)}")
    if limit.available > 0:
        buttons.append(
            [InlineKeyboardButton(text="➕ Ещё профиль инвестора", callback_data="inv:new")]
        )
    else:
        lines.append("Лимит достигнут: новый профиль возможен с подходящим Investor PRO.")
    await state.set_state(InvestorWizard.hub)
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def entry_investor(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user):
        return
    if not is_adult(db_user):
        await message.answer(ADULT_REQUIRED_MESSAGE, reply_markup=adult_confirm_keyboard())
        return
    await _show_investor_hub(message, session, db_user, state)


async def on_investor_select(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile_id = (callback.data or "").rsplit(":", 1)[-1]
    profile = await InvestorService(session).get_owned(uuid.UUID(profile_id), db_user.id)
    if profile is None:
        await callback.answer("Профиль не найден.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(investor_id=str(profile.id))
    if profile.review_status in {REVIEW_DRAFT, REVIEW_CHANGES, REVIEW_REJECTED}:
        await state.set_state(InvestorWizard.hub)
        has_draft = can_edit_review_state(profile.review_status)
        body = (
            "💼 Профиль инвестора\n\n"
            f"Имя: {escape(profile.display_name or 'не указано')}\n"
            f"Статус: {review_state_label(profile.review_status)}\n\n"
            "Статус и мандат подтверждаются только после проверки RAUNDO."
        )
        await callback.message.answer(body, reply_markup=hub_investor_keyboard(has_draft=has_draft))
        return
    await _show_investor_manage(callback.message, session, state, profile)


async def on_investor_new(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    user_id = db_user.id
    try:
        profile = await InvestorService(session).create_additional_draft(user_id)
        profile_id = profile.id
        await session.commit()
    except ProfileLimitError:
        await session.rollback()
        limits = await limits_for_user(session, user_id)
        await callback.answer(
            f"Лимит: {limits.investor.used} из {limits.investor.limit}. "
            "Для нового профиля нужен Investor PRO.",
            show_alert=True,
        )
        return
    await callback.answer()
    await state.update_data(
        investor_id=str(profile_id), sectors=[], stages=[], support_modes=[]
    )
    await state.set_state(InvestorWizard.display_name)
    await callback.message.answer(INVESTOR_NAME, reply_markup=wizard_nav())


async def on_investor_restore(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile_id = (callback.data or "").rsplit(":", 1)[-1]
    try:
        await InvestorService(session).restore_owned(
            uuid.UUID(profile_id), db_user.id
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Профиль восстановлен")
    profile = await InvestorService(session).get_owned(uuid.UUID(profile_id), db_user.id)
    await _show_investor_hub(callback.message, session, db_user, state)
    if profile is not None:
        await _show_investor_manage(callback.message, session, state, profile)


async def hub_create(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    try:
        await ensure_can_create(session, user_id=db_user.id, kind=KIND_INVESTOR)
    except ProfileLimitError:
        limits = await limits_for_user(session, db_user.id)
        await message.answer(
            "💼 На бесплатном тарифе можно создать один инвесторский профиль.\n\n"
            "Чтобы добавить ещё один, потребуется Investor PRO.\n"
            "Существующий профиль можно редактировать как обычно.\n\n"
            f"Мои профили инвестора: {limits.investor.used} из {limits.investor.limit}.",
            reply_markup=main_menu(),
        )
        return
    try:
        profile = await InvestorService(session).create_additional_draft(db_user.id)
        profile_id = profile.id
        await session.commit()
    except ProfileLimitError:
        await session.rollback()
        limits = await limits_for_user(session, db_user.id)
        await message.answer(
            "💼 На бесплатном тарифе можно создать один инвесторский профиль. "
            "Для нового потребуется Investor PRO.\n\n"
            f"Мои профили инвестора: {limits.investor.used} из {limits.investor.limit}.",
            reply_markup=main_menu(),
        )
        return
    await state.update_data(
        investor_id=str(profile_id), sectors=[], stages=[], support_modes=[]
    )
    await state.set_state(InvestorWizard.display_name)
    await message.answer(INVESTOR_NAME, reply_markup=wizard_nav())


async def hub_continue(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    if profile is None:
        await message.answer("Анкета не найдена.")
        return
    if profile.review_status not in {REVIEW_DRAFT, REVIEW_CHANGES, REVIEW_REJECTED}:
        await _show_investor_manage(message, session, state, profile)
        return
    await state.update_data(investor_id=str(profile.id))
    svc = InvestorService(session)
    sectors, stages = await svc.preference_codes(profile)
    await state.update_data(
        sectors=list(sectors),
        stages=list(stages),
        support_modes=list(profile.support_modes or []),
    )
    step = next_investor_step(profile, sector_codes=sectors, stage_codes=stages)
    await _prompt_investor_state(message, session, state, step, profile)


async def cancel_wizard(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Анкета прервана.", reply_markup=main_menu())


async def save_draft(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await session.commit()
    await state.clear()
    await message.answer("Черновик сохранён.", reply_markup=main_menu())


async def on_back(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    current = await state.get_state()
    profile = await _load(session, state, db_user)
    prev = previous_investor_step(current, profile)
    if prev is None:
        await message.answer("Некуда возвращаться.")
        return
    await _prompt_investor_state(message, session, state, prev, profile)


async def on_display_name(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    err = validate_name(message.text or "")
    if err:
        await message.answer(err)
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None:
        return
    await _prepare_investor_write(session, profile)
    profile.display_name = message.text.strip()
    await state.set_state(InvestorWizard.investor_type)
    await message.answer(INVESTOR_TYPE, reply_markup=investor_type_keyboard())


async def on_type_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_ITYPE) :]
    profile = await _editable_for_callback(callback, session, state, db_user)
    if profile is None or callback.message is None:
        return
    if not is_known_participant_type(code):
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await _prepare_investor_write(session, profile)
    profile.investor_type = code
    await callback.answer()
    await state.set_state(InvestorWizard.support_modes)
    await state.update_data(support_modes=list(profile.support_modes or []))
    selected = set(profile.support_modes or [])
    await callback.message.answer(INVESTOR_SUPPORT, reply_markup=wizard_nav())
    await callback.message.answer("Варианты:", reply_markup=support_mode_keyboard(selected))


async def on_support_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_SMOC) :]
    if code not in SUPPORT_MODE_LABELS:
        await callback.answer("Неизвестный формат", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("support_modes") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)
    await state.update_data(support_modes=list(selected))
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=support_mode_keyboard(selected))


async def on_support_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _editable_for_callback(callback, session, state, db_user)
    data = await state.get_data()
    selected = [c for c in (data.get("support_modes") or []) if c in SUPPORT_MODE_LABELS]
    if not selected:
        await callback.answer("Выберите хотя бы один формат.", show_alert=True)
        return
    if profile is None or callback.message is None:
        await callback.answer()
        return
    await _prepare_investor_write(session, profile)
    profile.support_modes = selected
    InvestorService(session).sync_mandate_flags(profile)
    await callback.answer()
    if investor_needs_organization(profile.investor_type or ""):
        await state.set_state(InvestorWizard.organization)
        await callback.message.answer(INVESTOR_ORG, reply_markup=wizard_nav())
    else:
        await state.set_state(InvestorWizard.position)
        await callback.message.answer(INVESTOR_POSITION, reply_markup=wizard_nav())


async def on_organization(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None or not message.text or len(message.text.strip()) < 2:
        if profile is not None:
            await message.answer("Укажите организацию.")
        return
    name = message.text.strip()[:256]
    await _prepare_investor_write(session, profile)
    profile.organization_name = name
    try:
        # Role is collected on the next step; claim without role first.
        org, membership = await OrganizationService(session).claim_organization(
            user_id=db_user.id,
            name=name,
            participant_type=profile.investor_type or "other",
            role=None,
            support_modes=list(profile.support_modes or []),
            claims_direct_investment=profile.claims_direct_investment,
        )
    except OrganizationServiceError as exc:
        await message.answer(exc.message)
        return
    except ProfileLimitError:
        await message.answer(
            "🏢 На бесплатном тарифе можно создать одну собственную организацию. "
            "Для дополнительной потребуется Organization PRO.\n\n"
            "Представительство в существующем фонде лимит не расходует.",
            reply_markup=main_menu(),
        )
        return
    profile.claimed_organization_id = org.id
    profile.affiliation_status = membership.status
    await state.set_state(InvestorWizard.position)
    await message.answer(
        INVESTOR_POSITION + "\nПринадлежность к организации пока только заявлена.",
        reply_markup=wizard_nav(),
    )


async def on_position(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in {BTN_BACK, BTN_CANCEL, BTN_SAVE}:
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None:
        return
    text = (message.text or "").strip()
    await _prepare_investor_write(session, profile)
    profile.position = None if text in {"-", "–", "—"} else text[:128]
    if profile.claimed_organization_id is not None:
        try:
            membership = await OrganizationService(session).sync_membership_role(
                user_id=db_user.id,
                organization_id=profile.claimed_organization_id,
                role=profile.position,
                claims_direct_investment=profile.claims_direct_investment,
            )
            profile.affiliation_status = membership.status
        except OrganizationServiceError as exc:
            await message.answer(exc.message)
            return
    await state.set_state(InvestorWizard.strategy)
    await message.answer(INVESTOR_STRATEGY, reply_markup=wizard_nav())


async def on_strategy(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    err = validate_strategy(message.text or "")
    if err:
        await message.answer(err)
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None:
        return
    await _prepare_investor_write(session, profile)
    profile.strategy_description = message.text.strip()
    await state.set_state(InvestorWizard.sectors)
    await state.update_data(sectors=[])
    catalog = CatalogRepository(session)
    sectors = [(s.code, s.name_ru) for s in await catalog.list_sectors()]
    await message.answer(
        "Выберите интересующие направления (минимум 1), затем «Готово»:",
        reply_markup=wizard_nav(),
    )
    await message.answer("Направления:", reply_markup=sector_toggle_keyboard(sectors, set()))


async def on_sector_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_SEC) :]
    data = await state.get_data()
    selected = set(data.get("sectors") or [])
    if code in selected:
        selected.remove(code)
    else:
        if len(selected) >= 8:
            await callback.answer("Не больше 8 направлений.", show_alert=True)
            return
        selected.add(code)
    await state.update_data(sectors=list(selected))
    from raundo.catalog_seed import SECTORS

    items = [(c, n) for c, n, _ in SECTORS]
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=sector_toggle_keyboard(items, selected)
        )


async def on_sectors_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    data = await state.get_data()
    selected = set(data.get("sectors") or [])
    if profile is None or callback.message is None:
        await callback.answer()
        return
    try:
        await InvestorService(session).set_sectors(profile, selected)
    except InvestorServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await state.set_state(InvestorWizard.stages)
    await state.update_data(stages=[])
    catalog = CatalogRepository(session)
    stages = [(s.code, s.label) for s in await catalog.list_stages()]
    await callback.answer()
    await callback.message.answer(INVESTOR_STAGES, reply_markup=wizard_nav())
    await callback.message.answer("Стадии:", reply_markup=stage_toggle_keyboard(stages, set()))


async def on_stage_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    code = (callback.data or "")[len(CB_STG) :]
    data = await state.get_data()
    selected = set(data.get("stages") or [])
    if code in selected:
        selected.remove(code)
    else:
        selected.add(code)
    await state.update_data(stages=list(selected))
    from raundo.catalog_seed import STAGES

    items = [(c, label) for c, label, _ in STAGES]
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=stage_toggle_keyboard(items, selected)
        )


async def _after_stages(
    message: Message, session: AsyncSession, profile, state: FSMContext
) -> None:
    if cheque_is_relevant(profile.investor_type, profile.support_modes):
        await state.set_state(InvestorWizard.check_size)
        await message.answer(INVESTOR_CHECK, reply_markup=check_nav())
    else:
        await _prepare_investor_write(session, profile)
        profile.check_discuss_individually = True
        profile.min_check_minor = None
        profile.max_check_minor = None
        profile.currency = None
        await state.set_state(InvestorWizard.geography)
        await message.answer(INVESTOR_GEO, reply_markup=geography_keyboard())


async def on_stages_done(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    data = await state.get_data()
    selected = set(data.get("stages") or [])
    if profile is None or callback.message is None:
        await callback.answer()
        return
    try:
        await InvestorService(session).set_stages(profile, selected)
    except InvestorServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer()
    await _after_stages(callback.message, session, profile, state)


async def on_check(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in {BTN_BACK, BTN_CANCEL, BTN_SAVE}:
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None:
        return
    text = (message.text or "").strip()
    await _prepare_investor_write(session, profile)
    if text == BTN_CHECK_INDIVIDUAL:
        profile.check_discuss_individually = True
        profile.min_check_minor = None
        profile.max_check_minor = None
        profile.currency = None
    else:
        parsed = parse_cheque_range_major(text)
        if parsed is None:
            await message.answer(
                "Формат: 100000 5000000 RUB (минимум не больше максимума) "
                "или кнопка «Обсуждаю индивидуально»."
            )
            return
        min_m, max_m, currency = parsed
        profile.check_discuss_individually = False
        profile.min_check_minor = min_m
        profile.max_check_minor = max_m
        profile.currency = currency
    await state.set_state(InvestorWizard.geography)
    await message.answer(INVESTOR_GEO, reply_markup=geography_keyboard())


async def on_geo_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_GEO) :]
    profile = await _editable_for_callback(callback, session, state, db_user)
    if profile is None or callback.message is None:
        return
    if code not in {"ru", "world"}:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return
    await _prepare_investor_write(session, profile)
    profile.geography = {"scope": code}
    await state.set_state(InvestorWizard.public_link)
    await callback.answer()
    await callback.message.answer(INVESTOR_LINK, reply_markup=wizard_nav())


async def on_public_link(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if (message.text or "") in _NAV_BUTTONS:
        return
    url = normalize_https_url(message.text or "")
    if url is None:
        await message.answer("Нужна корректная HTTPS-ссылка без логина/пароля в URL.")
        return
    profile = await _editable_for_message(message, session, state, db_user)
    if profile is None:
        return
    await _prepare_investor_write(session, profile)
    profile.public_reference_url = url
    await state.set_state(InvestorWizard.photo)
    await message.answer(INVESTOR_PHOTO, reply_markup=photos_nav(include_skip=True))


async def on_contact_policy(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    policy = (callback.data or "")[len(CB_CONTACT) :]
    profile = await _editable_for_callback(callback, session, state, db_user)
    if profile is None or callback.message is None:
        return
    await _prepare_investor_write(session, profile)
    profile.contact_policy = policy if policy in {"relay", "username"} else "relay"
    profile.show_contact_after_accept = policy == "username"
    profile.open_for_requests = True
    if policy == "username" and db_user.username:
        profile.contact_username = db_user.username
    svc = InvestorService(session)
    if profile.review_status == REVIEW_PENDING_CHANGES:
        from raundo.services.profile_lifecycle import withdraw_investor_sensitive_public

        if policy != "username":
            await withdraw_investor_sensitive_public(session, profile)
    await svc.register_owner_change(profile)
    await state.set_state(InvestorWizard.preview)
    await callback.answer()
    await _show_preview(callback.message, session, profile)


async def _go_contact(message: Message, state: FSMContext) -> None:
    await state.set_state(InvestorWizard.contact_policy)
    await message.answer(INVESTOR_CONTACT, reply_markup=contact_policy_keyboard())


async def on_investor_photo(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not message.photo:
        return
    profile = await _editable_for_message(message, session, db_user=db_user, state=state)
    if profile is None:
        return
    photo = message.photo[-1]
    try:
        await InvestorService(session).set_photo(
            profile,
            telegram_file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
        )
    except InvestorServiceError as exc:
        await message.answer(exc.message)
        return
    await message.answer("Изображение сохранено. Можно заменить, отправив другое фото.")


async def on_investor_photo_text(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    text = message.text or ""
    if text in {BTN_BACK, BTN_CANCEL, BTN_SAVE}:
        return
    profile = await _editable_for_message(message, session, db_user=db_user, state=state)
    if profile is None:
        return
    if text in {BTN_SKIP, BTN_PHOTOS_DONE}:
        await _go_contact(message, state)
        return
    await message.answer("Пришлите фотографию или нажмите «Пропустить», чтобы перейти дальше.")


async def _show_preview(message: Message, session: AsyncSession, profile) -> None:
    svc = InvestorService(session)
    sectors, stages = await svc.preference_codes(profile)
    view = build_investor_card_view(
        profile,
        sector_codes=sorted(sectors),
        stage_codes=sorted(stages),
        include_owner_private=True,
    )
    html = compact_investor_html(view, owner_preview=True)
    nav = preview_nav()
    if view.photo_file_id:
        try:
            await message.answer_photo(
                photo=view.photo_file_id, caption=html, parse_mode="HTML", reply_markup=nav
            )
            return
        except Exception:
            pass
    await message.answer(html, parse_mode="HTML", reply_markup=nav)


async def on_preview(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    if profile is None:
        return
    text = message.text or ""
    if text == BTN_MORE:
        sectors, stages = await InvestorService(session).preference_codes(profile)
        view = build_investor_card_view(
            profile,
            sector_codes=sorted(sectors),
            stage_codes=sorted(stages),
            include_owner_private=True,
        )
        await message.answer(
            details_investor_html(view, owner_preview=True),
            parse_mode="HTML",
            reply_markup=preview_nav(),
        )
        return
    if text == BTN_EDIT:
        if profile.review_status in {
            REVIEW_APPROVED,
            REVIEW_SUBMITTED,
            REVIEW_UNDER_REVIEW,
            REVIEW_PENDING_CHANGES,
        }:
            await message.answer(REEDIT_MODERATION_NOTICE)
        await state.set_state(InvestorWizard.edit_pick)
        await message.answer("Что изменить? Остальные поля сохранятся.", reply_markup=preview_nav())
        await message.answer("Поля:", reply_markup=investor_edit_fields_keyboard())
        return
    if text == BTN_SAVE:
        await save_draft(message, session, db_user, state)
        return
    if text != BTN_SUBMIT:
        await message.answer("Выберите действие на клавиатуре.")
        return
    plan = _investor_offer_plan(profile)
    premium = PremiumService(session)
    active = False
    if plan == PLAN_INVESTOR_PRO:
        active = await premium.has_active_user_plan(profile.user_id, PLAN_INVESTOR_PRO)
    elif profile.claimed_organization_id is not None:
        active = await premium.has_active_org_pro(profile.claimed_organization_id)
    if active:
        await _commit_investor_submit(message, session, db_user, state, profile)
        return
    await state.set_state(InvestorWizard.premium_offer)
    await _show_investor_premium_offer(message, session, profile)


def _investor_offer_plan(profile) -> str:
    if investor_needs_organization(profile.investor_type or ""):
        return PLAN_ORGANIZATION_PRO
    return PLAN_INVESTOR_PRO


async def _show_investor_premium_offer(message: Message, session: AsyncSession, profile) -> None:
    plan = _investor_offer_plan(profile)
    premium = PremiumService(session)
    active = False
    if plan == PLAN_INVESTOR_PRO:
        active = await premium.has_active_user_plan(profile.user_id, PLAN_INVESTOR_PRO)
    elif profile.claimed_organization_id is not None:
        active = await premium.has_active_org_pro(profile.claimed_organization_id)
    if active:
        text = PREMIUM_ALREADY_ACTIVE.format(title=PLAN_TITLES[plan])
    elif plan == PLAN_ORGANIZATION_PRO:
        text = PREMIUM_OFFER_ORG
    else:
        text = PREMIUM_OFFER_INVESTOR
    await message.answer(text, reply_markup=premium_offer_nav())


async def _commit_investor_submit(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext, profile
) -> None:
    await state.update_data(
        pending_submit_kind="investor", pending_submit_id=str(profile.id)
    )
    if not await has_publication_consent(session, db_user.id):
        await message.answer(
            PUBLICATION_CONSENT_TEXT, reply_markup=publication_consent_keyboard()
        )
        return
    try:
        InvestorService(session).sync_mandate_flags(profile)
        await InvestorService(session).submit_for_review(profile, db_user.id)
        await AnalyticsService(session).track("investor_profile_submitted", user_id=db_user.id)
        await NotificationService(session).enqueue(
            kind="investor.submitted",
            dedupe_key=f"investor_submitted:{profile.id}:{profile.current_revision}",
            recipient_user_id=db_user.id,
            resource_type="investor",
            resource_id=str(profile.id),
            safe_payload={"review_status": REVIEW_SUBMITTED},
        )
        await session.commit()
    except InvestorServiceError as exc:
        await session.rollback()
        await message.answer(exc.message)
        return
    except Exception:
        await session.rollback()
        await message.answer("Не удалось отправить анкету. Попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer(SUBMITTED_INVESTOR, reply_markup=main_menu())


async def on_investor_manage(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    if profile is None:
        await message.answer("Анкета не найдена.")
        return
    text = message.text or ""
    if text == BTN_CANCEL:
        await state.clear()
        await message.answer("Вы в главном меню.", reply_markup=main_menu())
        return
    if text == BTN_VIEW_CARD:
        await state.set_state(InvestorWizard.preview)
        await _show_preview(message, session, profile)
        return
    if text == BTN_EDIT:
        if profile.review_status in {
            REVIEW_APPROVED,
            REVIEW_SUBMITTED,
            REVIEW_UNDER_REVIEW,
            REVIEW_PENDING_CHANGES,
        }:
            await message.answer(REEDIT_MODERATION_NOTICE)
        await state.set_state(InvestorWizard.edit_pick)
        await message.answer("Что изменить? Остальные поля сохранятся.", reply_markup=preview_nav())
        await message.answer("Поля:", reply_markup=investor_edit_fields_keyboard())
        return
    if text == BTN_DELETE_PROFILE:
        await state.set_state(InvestorWizard.delete_confirm)
        await message.answer(DELETE_PROFILE_PROMPT, reply_markup=delete_confirm_keyboard())
        return
    await message.answer("Выберите действие на клавиатуре.", reply_markup=manage_investor_keyboard())


async def on_investor_delete_confirm(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    if profile is None:
        await state.clear()
        await message.answer("Анкета не найдена.", reply_markup=main_menu())
        return
    text = message.text or ""
    if text == BTN_CANCEL_DELETE:
        await _show_investor_manage(message, session, state, profile)
        return
    if text != BTN_CONFIRM_DELETE:
        await message.answer("Подтвердите или отмените удаление.", reply_markup=delete_confirm_keyboard())
        return
    svc = InvestorService(session)
    try:
        await svc.delete_owned(profile, db_user.id)
        await session.commit()
    except InvestorServiceError as exc:
        await session.rollback()
        await message.answer(exc.message)
        return
    await state.clear()
    await message.answer(DELETED_INVESTOR, reply_markup=main_menu())


async def on_investor_premium_offer(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    profile = await _load(session, state, db_user)
    if profile is None:
        return
    text = message.text or ""
    if text == BTN_BACK_TO_FORM:
        await state.set_state(InvestorWizard.preview)
        await _show_preview(message, session, profile)
        return
    if text == BTN_PREMIUM_VIEW:
        plan = _investor_offer_plan(profile)
        await message.answer(
            PREMIUM_INTRO,
            reply_markup=premium_root_keyboard(dev_grants_available=False),
        )
        await message.answer(plan_features_text(plan), reply_markup=premium_offer_nav())
        return
    if text == BTN_CONTINUE_FREE:
        await _commit_investor_submit(message, session, db_user, state, profile)
        return
    await message.answer("Выберите действие на клавиатуре.", reply_markup=premium_offer_nav())


async def on_investor_edit_cb(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    code = (callback.data or "")[len(CB_IEDT) :]
    profile = await _editable_for_callback(callback, session, state, db_user)
    if profile is None or callback.message is None:
        return
    if code == "back":
        await state.set_state(InvestorWizard.preview)
        await callback.answer()
        await _show_preview(callback.message, session, profile)
        return
    mapping = {
        "photo": InvestorWizard.photo.state,
        "name": InvestorWizard.display_name.state,
        "type": InvestorWizard.investor_type.state,
        "org": InvestorWizard.organization.state,
        "position": InvestorWizard.position.state,
        "strategy": InvestorWizard.strategy.state,
        "sectors": InvestorWizard.sectors.state,
        "stages": InvestorWizard.stages.state,
        "check": InvestorWizard.check_size.state,
        "geography": InvestorWizard.geography.state,
        "link": InvestorWizard.public_link.state,
        "contact": InvestorWizard.contact_policy.state,
    }
    step = mapping.get(code)
    if step is None:
        await callback.answer("Неизвестное поле.", show_alert=True)
        return
    await callback.answer()
    await _prompt_investor_state(callback.message, session, state, step, profile)


def create_router() -> Router:
    router = Router(name="investor")
    router.message.register(entry_investor, Command("investor"))
    router.message.register(entry_investor, F.text == MENU_INVESTOR)
    router.callback_query.register(on_investor_select, F.data.startswith("inv:sel:"))
    router.callback_query.register(on_investor_new, F.data == "inv:new")
    router.callback_query.register(on_investor_restore, F.data.startswith("inv:res:"))
    router.message.register(on_investor_manage, InvestorWizard.manage, F.text)
    router.message.register(on_investor_delete_confirm, InvestorWizard.delete_confirm, F.text)
    router.message.register(hub_create, InvestorWizard.hub, F.text == BTN_CREATE_INVESTOR)
    router.message.register(hub_continue, InvestorWizard.hub, F.text == BTN_CONTINUE)
    router.message.register(cancel_wizard, StateFilter(InvestorWizard), F.text == BTN_CANCEL)
    router.message.register(save_draft, StateFilter(InvestorWizard), F.text == BTN_SAVE)
    router.message.register(on_back, StateFilter(InvestorWizard), F.text == BTN_BACK)

    router.message.register(on_display_name, InvestorWizard.display_name, F.text)
    router.callback_query.register(
        on_type_cb, InvestorWizard.investor_type, F.data.startswith(CB_ITYPE)
    )
    router.callback_query.register(
        on_support_toggle, InvestorWizard.support_modes, F.data.startswith(CB_SMOC)
    )
    router.callback_query.register(
        on_support_done, InvestorWizard.support_modes, F.data == CB_DONE_SM
    )
    router.message.register(on_organization, InvestorWizard.organization, F.text)
    router.message.register(on_position, InvestorWizard.position, F.text)
    router.message.register(on_strategy, InvestorWizard.strategy, F.text)
    router.callback_query.register(
        on_sector_toggle, InvestorWizard.sectors, F.data.startswith(CB_SEC)
    )
    router.callback_query.register(
        on_sectors_done, InvestorWizard.sectors, F.data == CB_DONE_SEC
    )
    router.callback_query.register(
        on_stage_toggle, InvestorWizard.stages, F.data.startswith(CB_STG)
    )
    router.callback_query.register(
        on_stages_done, InvestorWizard.stages, F.data == CB_DONE_STG
    )
    router.message.register(on_check, InvestorWizard.check_size, F.text)
    router.callback_query.register(
        on_geo_cb, InvestorWizard.geography, F.data.startswith(CB_GEO)
    )
    router.message.register(on_public_link, InvestorWizard.public_link, F.text)
    router.message.register(on_investor_photo, InvestorWizard.photo, F.photo)
    router.message.register(on_investor_photo_text, InvestorWizard.photo, F.text)
    router.callback_query.register(
        on_contact_policy, InvestorWizard.contact_policy, F.data.startswith(CB_CONTACT)
    )
    router.callback_query.register(
        on_investor_edit_cb, InvestorWizard.edit_pick, F.data.startswith(CB_IEDT)
    )
    router.message.register(on_preview, InvestorWizard.preview, F.text)
    router.message.register(on_preview, InvestorWizard.edit_pick, F.text)
    router.message.register(on_investor_premium_offer, InvestorWizard.premium_offer, F.text)
    return router
