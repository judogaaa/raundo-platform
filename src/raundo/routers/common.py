""" /start, consents, menu placeholders and public commands."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.config import Settings, get_settings
from raundo.keyboards import (
    CALLBACK_ACCEPT_CONSENT,
    CALLBACK_AGE_PREFIX,
    CALLBACK_DELETE_CANCEL,
    CALLBACK_DELETE_CONFIRM,
    CALLBACK_DELETE_OPEN,
    CALLBACK_SHOW_PRIVACY,
    CALLBACK_SHOW_TERMS,
    age_keyboard,
    deletion_keyboard,
    main_menu,
    onboarding_keyboard,
)
from raundo.models.admin_panel import SupportTicket
from raundo.models.user import User
from raundo.routers.support import SupportFlow
from raundo.services.account_deletion import delete_account
from raundo.services.age import AGE_ADULT, AGE_MINOR, confirm_age
from raundo.services.analytics import AnalyticsService
from raundo.services.consents import (
    CONSENT_PROFILE_PUBLICATION,
    ConsentService,
)
from raundo.services.notifications import NotificationService
from raundo.services.support_tickets import (
    USER_ACTION_STATUSES,
    Attachment,
    SupportError,
    add_user_message,
)
from raundo.state.consent import ConsentStates
from raundo.texts import (
    HELP_TEXT,
    NEED_CONSENT,
    PAYSUPPORT,
    WELCOME,
)


def _consent_service(session: AsyncSession, settings: Settings) -> ConsentService:
    return ConsentService(session)


ONBOARDING_TEXT = (
    "👋 <b>Добро пожаловать в RAUNDO!</b>\n\n"
    "Создавайте стартапы, находите инвесторов и знакомьтесь с участниками платформы.\n\n"
    "Продолжая, вы принимаете Пользовательское соглашение и подтверждаете "
    "ознакомление с Политикой конфиденциальности.\n\n"
    "Отдельное согласие на обработку персональных данных:"
)

AGE_ONBOARDING_TEXT = (
    "🎂 <b>Ваш возраст</b>\n\n"
    "RAUNDO доступен с 14 лет. Premium и инвесторские функции только с 18.\n"
    "Это ваше подтверждение, а не проверка документа."
)

DELETE_PROMPT_TEXT = (
    "🗑 <b>Удаление аккаунта</b>\n\n"
    "Анкеты и профили будут скрыты сразу, персональные данные анонимизированы.\n"
    "Платёжные записи сохраняются только в объёме, требуемом законом.\n\n"
    "Удалить аккаунт без возможности восстановления?"
)


async def _show_onboarding(
    message: Message,
    state: FSMContext,
    db_user: User,
    *,
    consent: bool | None = None,
) -> None:
    stored = await state.get_data()
    selected = bool(stored.get("onb_consent")) if consent is None else consent
    age_required = db_user.age_bracket not in {AGE_MINOR, AGE_ADULT}
    await state.set_state(ConsentStates.awaiting_acceptance)
    await state.update_data(onb_consent=selected)
    await message.answer(
        ONBOARDING_TEXT,
        reply_markup=onboarding_keyboard(consent=selected, age_required=age_required),
    )


async def _require_consent(
    message: Message,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    state: FSMContext,
) -> bool:
    consents = _consent_service(session, settings)
    if await consents.has_required_consents(db_user.id):
        return True
    await _show_onboarding(message, state, db_user, consent=False)
    return False


async def start(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    settings = get_settings()
    consents = _consent_service(session, settings)
    analytics = AnalyticsService(session)
    await analytics.track("bot_started", user_id=db_user.id)

    if not await consents.has_required_consents(db_user.id):
        await _show_onboarding(message, state, db_user, consent=False)
        return

    if db_user.age_bracket not in {AGE_MINOR, AGE_ADULT}:
        await state.set_state(ConsentStates.awaiting_age)
        await message.answer(AGE_ONBOARDING_TEXT, reply_markup=age_keyboard())
        return

    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


async def onboarding_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    stored = await state.get_data()
    selected = not bool(stored.get("onb_consent"))
    await state.update_data(onb_consent=selected)
    await callback.answer("Согласие отмечено" if selected else "Согласие снято")
    if callback.message is not None:
        age_required = False
        try:
            await callback.message.edit_reply_markup(
                reply_markup=onboarding_keyboard(consent=selected, age_required=age_required)
            )
        except Exception:  # noqa: BLE001 - stale message is not fatal
            pass


async def _finish_onboarding(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    *,
    grant_consents: bool,
) -> None:
    if grant_consents:
        consents = ConsentService(session)
        try:
            await consents.accept_required(db_user.id, source="bot_onboarding")
            AnalyticsService(session)
            notifications = NotificationService(session)
            await notifications.enqueue(
                kind="user.consent_accepted",
                dedupe_key=f"consent:{db_user.id}:{consents.policy_version}",
                recipient_user_id=db_user.id,
                safe_payload={"policy_version": consents.policy_version},
            )
            await session.commit()
        except Exception:
            await session.rollback()
            await callback.answer(
                "Не удалось сохранить согласие. Попробуйте ещё раз.", show_alert=True
            )
            return
    await state.clear()
    await callback.answer("Готово")
    if callback.message is not None:
        await callback.message.answer(WELCOME, reply_markup=main_menu())


async def choose_age(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    data = callback.data or ""
    bracket = data[len(CALLBACK_AGE_PREFIX) :]
    if bracket not in {AGE_MINOR, AGE_ADULT}:
        await callback.answer("Неизвестный вариант.", show_alert=True)
        return

    stored = await state.get_data()
    consent_selected = bool(stored.get("onb_consent"))
    service = ConsentService(session)
    already = await service.has_required_consents(db_user.id)
    if not consent_selected and not already:
        await callback.answer(
            "Отметьте согласие на обработку персональных данных.", show_alert=True
        )
        return

    confirm_age(db_user, bracket)
    await session.commit()
    label = "14-17" if bracket == AGE_MINOR else "18+"
    await callback.answer(f"Возраст: {label}")
    await _finish_onboarding(
        callback, session, db_user, state, grant_consents=not already
    )
    if callback.message is not None and bracket == AGE_MINOR:
        await callback.message.answer(
            "Premium и инвесторские функции станут доступны после подтверждения 18+."
        )


async def onboarding_continue(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    stored = await state.get_data()
    consent_selected = bool(stored.get("onb_consent"))
    service = ConsentService(session)
    already = await service.has_required_consents(db_user.id)
    if not consent_selected and not already:
        await callback.answer(
            "Отметьте согласие на обработку персональных данных.", show_alert=True
        )
        return
    await callback.answer()
    await _finish_onboarding(callback, session, db_user, state, grant_consents=not already)


async def legacy_consent_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Откройте /start, чтобы продолжить регистрацию.", show_alert=True)

async def grant_publication_consent(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    service = ConsentService(session)
    try:
        await service.grant(
            db_user.id,
            CONSENT_PROFILE_PUBLICATION,
            source="publish_prompt",
        )
        await session.commit()
    except Exception:
        await session.rollback()
        await callback.answer("Не удалось сохранить согласие.", show_alert=True)
        return

    data = await state.get_data()
    kind = data.get("pending_submit_kind")
    target_id = data.get("pending_submit_id")
    submitted = False
    if kind and target_id:
        try:
            target_uuid = uuid.UUID(str(target_id))
            if kind == "startup":
                from raundo.models.startup import Startup
                from raundo.services.startups import StartupService

                startup = await session.get(Startup, target_uuid)
                if startup is not None and startup.owner_user_id == db_user.id:
                    await StartupService(session).submit_for_review(startup, db_user.id)
                    submitted = True
            elif kind == "investor":
                from raundo.models.investor import InvestorProfile
                from raundo.services.investors import InvestorService

                profile = await session.get(InvestorProfile, target_uuid)
                if profile is not None and profile.user_id == db_user.id:
                    InvestorService(session).sync_mandate_flags(profile)
                    await InvestorService(session).submit_for_review(profile, db_user.id)
                    submitted = True
            if submitted:
                await session.commit()
        except Exception:
            await session.rollback()
            submitted = False

    await state.clear()
    await callback.answer("Согласие сохранено")
    if callback.message is not None:
        if submitted:
            await callback.message.answer(
                "✅ Анкета отправлена на модерацию. Мы уведомим вас после проверки.",
                reply_markup=main_menu(),
            )
        else:
            await callback.message.answer(
                "Согласие на публикацию сохранено. Вернитесь к анкете и нажмите "
                "«Отправить на проверку».",
                reply_markup=main_menu(),
            )


async def delete_account_command(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(DELETE_PROMPT_TEXT, reply_markup=deletion_keyboard())


async def delete_account_open(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(DELETE_PROMPT_TEXT, reply_markup=deletion_keyboard())


async def delete_account_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    report = await delete_account(session, db_user)
    await session.commit()
    await state.clear()
    await callback.answer("Аккаунт удалён")
    if callback.message is not None:
        await callback.message.answer(
            "Аккаунт удалён. Анкеты скрыты, персональные данные анонимизированы.\n"
            f"Профилей скрыто: {int(report.get('startups', 0)) + int(report.get('investor_profiles', 0)) + int(report.get('organizations', 0))}.\n"
            "Если понадобится помощь - напишите в поддержку.",
            reply_markup=main_menu(),
        )


async def delete_account_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        await callback.message.answer("Удаление отменено.", reply_markup=main_menu())


PUBLIC_SITE = get_settings().checkout_base_url.rstrip("/")

LEGAL_LINKS_TEXT = (
    "⚖️ <b>Документы RAUNDO</b>\n\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/terms\">Соглашение</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/privacy\">Политика конфиденциальности</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/offer\">Оферта Premium</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/payments\">Оплата и возвраты</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/requisites\">Реквизиты</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/moderation\">Правила модерации</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal/minors\">Пользователи 14-17 лет</a>\n"
    f"• <a href=\"{PUBLIC_SITE}/legal\">Все документы и согласия</a>\n\n"
    f"Тарифы: <a href=\"{PUBLIC_SITE}/premium\">RAUNDO Premium</a>"
)


async def show_terms_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"Соглашение RAUNDO: {PUBLIC_SITE}/legal/terms", disable_web_page_preview=True
        )


async def show_privacy_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"Политика конфиденциальности RAUNDO: {PUBLIC_SITE}/legal/privacy",
            disable_web_page_preview=True,
        )


async def terms_command(message: Message) -> None:
    await message.answer(
        f"Соглашение RAUNDO: {PUBLIC_SITE}/legal/terms", disable_web_page_preview=True
    )


async def privacy_command(message: Message) -> None:
    await message.answer(
        f"Политика конфиденциальности RAUNDO: {PUBLIC_SITE}/legal/privacy",
        disable_web_page_preview=True,
    )


async def legal_command(message: Message) -> None:
    await message.answer(LEGAL_LINKS_TEXT, disable_web_page_preview=True)


async def paysupport_command(message: Message) -> None:
    await message.answer(PAYSUPPORT)


async def help_message(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu())


async def cancel_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Готово. Вы в главном меню.",
        reply_markup=main_menu(),
    )


async def consent_gate_message(
    message: Message, state: FSMContext, db_user: User
) -> None:
    stored = await state.get_data()
    selected = bool(stored.get("onb_consent"))
    await message.answer(
        NEED_CONSENT,
        reply_markup=onboarding_keyboard(
            consent=selected,
            age_required=db_user.age_bracket not in {AGE_MINOR, AGE_ADULT},
        ),
    )


async def age_gate_message(message: Message) -> None:
    await message.answer(AGE_ONBOARDING_TEXT, reply_markup=age_keyboard())


async def _support_continuation(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> bool:
    """Attach an unsolicited message to an open ticket or ask which one.

    Support conversations continue in the same ticket: never create a new
    ticket and never guess when several are open.
    """
    tickets = list(
        (
            await session.execute(
                select(SupportTicket)
                .where(
                    SupportTicket.user_id == db_user.id,
                    SupportTicket.status.in_(USER_ACTION_STATUSES),
                )
                .order_by(SupportTicket.updated_at.desc())
                .limit(5)
            )
        ).scalars()
    )
    if not tickets:
        return False
    text = message.text or message.caption or ""
    attachment = Attachment.from_telegram(message)
    if not text.strip() and attachment is None:
        return False
    if len(tickets) == 1:
        ticket = tickets[0]
        try:
            await add_user_message(
                session,
                ticket=ticket,
                user_id=db_user.id,
                body=text,
                attachment=attachment,
            )
            await session.flush()
            await session.commit()
        except SupportError:
            await session.rollback()
            return False
        await message.answer(
            f"✅ Добавили в обращение {ticket.public_number}. "
            "Сотрудник увидит сообщение и ответит в этом чате.",
            reply_markup=main_menu(),
        )
        return True
    await state.update_data(
        pending_support_text=text,
        pending_attachment_kind=attachment.kind if attachment else None,
        pending_attachment_file_id=attachment.file_id if attachment else None,
        pending_attachment_name=attachment.name if attachment else None,
        pending_attachment_size=attachment.size if attachment else None,
    )
    await state.set_state(SupportFlow.choose_ticket)
    await message.answer(
        "К какому обращению добавить сообщение?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{ticket.public_number} · {ui.ticket_status(ticket.status)}",
                        callback_data=f"sv:pick:{ticket.id}",
                    )
                ]
                for ticket in tickets
            ]
        ),
    )
    return True


async def unknown_private_message(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    settings = get_settings()
    if not await _require_consent(message, session, db_user, settings, state):
        return
    if not (message.text or "").startswith("/"):
        try:
            if await _support_continuation(message, session, db_user, state):
                return
        except Exception:  # noqa: BLE001 - never block the fallback on support
            await session.rollback()
    await message.answer(
        "Не понял сообщение. Откройте раздел кнопкой внизу или посмотрите подсказку: /help.",
        reply_markup=main_menu(),
    )


def create_router() -> Router:
    """Fresh router instance: required so tests can build many Dispatchers."""
    router = Router(name="common")
    router.message.register(start, CommandStart())
    router.callback_query.register(legacy_consent_button, F.data == CALLBACK_ACCEPT_CONSENT)
    router.callback_query.register(grant_publication_consent, F.data == "consent:pub:grant")
    router.callback_query.register(show_terms_callback, F.data == CALLBACK_SHOW_TERMS)
    router.callback_query.register(show_privacy_callback, F.data == CALLBACK_SHOW_PRIVACY)
    router.message.register(terms_command, Command("terms"))
    router.message.register(privacy_command, Command("privacy"))
    router.message.register(legal_command, Command("legal"))
    router.message.register(paysupport_command, Command("paysupport"))
    router.message.register(help_message, Command("help"))
    router.message.register(cancel_message, Command("cancel"))
    # Account deletion must stay available even while consents are pending.
    router.message.register(delete_account_command, Command("delete"))
    router.callback_query.register(delete_account_open, F.data == CALLBACK_DELETE_OPEN)
    router.callback_query.register(delete_account_confirm, F.data == CALLBACK_DELETE_CONFIRM)
    router.callback_query.register(delete_account_cancel, F.data == CALLBACK_DELETE_CANCEL)
    router.callback_query.register(onboarding_toggle, F.data == "onb:consent")
    router.callback_query.register(onboarding_continue, F.data == "onb:continue")
    router.callback_query.register(choose_age, F.data.startswith(CALLBACK_AGE_PREFIX))
    # Every menu label and section command is owned by its own router
    # (feed, startup, investor, premium, support, profile). /admin is first in
    # the root router, so this file only answers the consent gate and anything
    # that has no real handler.
    router.message.register(consent_gate_message, ConsentStates.awaiting_acceptance)
    router.message.register(age_gate_message, ConsentStates.awaiting_age)
    router.message.register(unknown_private_message)
    return router
