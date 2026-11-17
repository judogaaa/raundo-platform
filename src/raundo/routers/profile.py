""" /profile summary and /settings with real persisted preferences."""

from __future__ import annotations

import uuid
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.config import get_settings
from raundo.keyboards import (
    CALLBACK_CONSENT_REVOKE_PREFIX,
    CALLBACK_CONSENTS_OPEN,
    CALLBACK_DELETE_OPEN,
    MENU_FAVORITES,
    MENU_PROFILE,
    MENU_REQUESTS,
    MENU_SETTINGS,
    consents_manage_keyboard,
    data_revoke_keyboard,
    main_menu,
)
from raundo.models.billing import UserCollection, UserCollectionItem
from raundo.models.contact import REQUEST_PENDING
from raundo.models.feed import StartupReaction
from raundo.models.investor import InvestorProfile
from raundo.models.startup import Startup
from raundo.models.system import OutboxEvent
from raundo.models.user import User, UserProfile
from raundo.routers.common import _require_consent
from raundo.services import contact_requests as contacts
from raundo.services.age import age_label
from raundo.services.community_plus import CommunityPlusService
from raundo.services.consents import (
    CONSENT_PROFILE_PUBLICATION,
    OPTIONAL_KINDS,
    ConsentService,
)
from raundo.services.contact_requests import ContactError
from raundo.services.premium import PremiumService


async def _profile_row(session: AsyncSession, user_id) -> UserProfile:
    profile = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


def _profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔖 Избранное", callback_data="profile:favorites"),
                InlineKeyboardButton(text="🤝 Запросы", callback_data="profile:requests"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="profile:settings"),
                InlineKeyboardButton(text="⭐ Premium", callback_data="profile:premium"),
            ],
        ]
    )


async def _profile_text(session: AsyncSession, db_user: User) -> str:
    from raundo.models.organization import FundingOrganization
    from raundo.services.profile_limits import limits_for_user

    startups = [
        s
        for s in (
            await session.execute(select(Startup).where(Startup.owner_user_id == db_user.id))
        ).scalars()
        if s.deleted_at is None
    ]
    investors = [
        p
        for p in (
            await session.execute(
                select(InvestorProfile).where(InvestorProfile.user_id == db_user.id)
            )
        ).scalars()
        if p.deleted_at is None
    ]
    own_orgs = list(
        (
            await session.execute(
                select(FundingOrganization).where(
                    FundingOrganization.created_by_user_id == db_user.id
                )
            )
        ).scalars()
    )
    favorites = await session.scalar(
        select(func.count())
        .select_from(UserCollectionItem)
        .join(UserCollection, UserCollection.id == UserCollectionItem.collection_id)
        .where(UserCollection.user_id == db_user.id)
    )
    reactions = await session.scalar(
        select(func.count()).select_from(StartupReaction).where(StartupReaction.user_id == db_user.id)
    )
    subscriptions = await PremiumService(session).my_subscriptions(db_user.id)
    limits = await limits_for_user(session, db_user.id)
    incoming = await contacts.requests_for_owner(session, db_user.id)
    outgoing = await contacts.requests_for_requester(session, db_user.id)
    pending_in = [row for row in incoming if row[0].status == REQUEST_PENDING]

    name = " ".join(part for part in (db_user.first_name, db_user.last_name) if part)
    who = ui.esc(name or "Участник RAUNDO")
    if db_user.username:
        who = f"{who} · @{ui.esc(db_user.username)}"

    parts: list[str] = ["🚀 <b>Мой профиль</b>", who, ""]

    startup_plan = "Startup PRO" if limits.startup.plan_code else None
    parts.append(ui.limit_line("Стартапы", limits.startup.used, limits.startup.limit, startup_plan))
    if startups:
        for s in startups:
            parts.append(f"• {ui.esc(s.name or 'Без названия')} · {ui.status(s.review_state)}")
    else:
        parts.append("• Пока нет. Создать: /startup")
    parts.append("")

    investor_plan = "Investor PRO" if limits.investor.plan_code else None
    parts.append(
        ui.limit_line("Инвесторские профили", limits.investor.used, limits.investor.limit, investor_plan)
    )
    if investors:
        for index, p in enumerate(investors, start=1):
            parts.append(
                f"• {ui.esc(p.display_name or f'Профиль {index}')} · {ui.status(p.review_status)}"
            )
    else:
        parts.append("• Пока нет. Создать: /investor")
    parts.append("")

    org_plan = "Organization PRO" if limits.organization.plan_code else None
    parts.append(
        ui.limit_line("Организации", limits.organization.used, limits.organization.limit, org_plan)
    )
    if own_orgs:
        for org in own_orgs:
            parts.append(f"• {ui.esc(org.name)} · {ui.status(org.review_status)}")
    else:
        parts.append("• Своих организаций нет")
    parts.append("")

    parts.append(f"🔖 Избранное: {int(favorites or 0)} · Оценок: {int(reactions or 0)}")
    if subscriptions:
        titles = ", ".join(ui.plan_title(s.plan_code) for s in subscriptions)
        parts.append(f"⭐ Premium: {titles}")
    else:
        parts.append("⭐ Premium: нет активных подписок")
    if pending_in:
        parts.append(f"🤝 Запросы на знакомство: новых {len(pending_in)}")
    elif outgoing:
        latest = outgoing[0]
        parts.append(
            f"🤝 Последний запрос: «{ui.esc(latest[1].name or 'Проект')}» · "
            f"{ui.request_status(latest[0].status)}"
        )

    return ui.truncate("\n".join(parts))


async def _notification_enabled(session: AsyncSession, user_id) -> bool:
    pref = await CommunityPlusService(session).notification_pref(user_id)
    return bool(pref.enabled)


async def _settings_text(session: AsyncSession, db_user: User) -> str:
    profile = await _profile_row(session, db_user.id)
    consents = ConsentService(session)
    has_consents = await consents.has_required_consents(db_user.id)
    active = await consents.active(db_user.id)
    notify = await _notification_enabled(session, db_user.id)
    contact = {
        "relay": "через бота",
        "username": "Telegram username после принятия запроса",
    }.get(profile.contact_mode, "через бота")
    reveal = (
        "показывать после принятия запроса"
        if profile.show_contact_after_accept
        else "не показывать"
    )
    return "\n".join(
        [
            "⚙️ <b>Настройки</b>",
            "",
            f"Возраст: {ui.esc(age_label(db_user.age_bracket))}",
            f"Связь: {ui.esc(contact)}",
            f"Контакт после запроса: {ui.esc(reveal)}",
            f"Дополнительные уведомления: {'включены' if notify else 'выключены'}",
            f"Согласия: {'приняты' if has_consents else 'не приняты'}",
            f"Распространение анкеты: {'включено' if CONSENT_PROFILE_PUBLICATION in active else 'выключено'}",
            f"Telegram ID: {ui.code(db_user.telegram_id)}",
            "",
            "Основные уведомления о статусе анкет и запросах приходят всегда.",
            "Изменения применяются сразу.",
        ]
    )


def _settings_keyboard(profile: UserProfile, notify: bool) -> InlineKeyboardMarkup:
    relay = "✅ " if profile.contact_mode == "relay" else ""
    username = "✅ " if profile.contact_mode == "username" else ""
    reveal_on = "✅ " if profile.show_contact_after_accept else ""
    reveal_off = "✅ " if not profile.show_contact_after_accept else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{relay}Через бота", callback_data="settings:contact:relay"),
                InlineKeyboardButton(
                    text=f"{username}Username", callback_data="settings:contact:username"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{reveal_on}Показывать после запроса",
                    callback_data="settings:reveal:on",
                ),
                InlineKeyboardButton(
                    text=f"{reveal_off}Не показывать",
                    callback_data="settings:reveal:off",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if notify else ''}Уведомления включены",
                    callback_data="settings:notify:on",
                ),
                InlineKeyboardButton(
                    text=f"{'✅ ' if not notify else ''}Выключены",
                    callback_data="settings:notify:off",
                ),
            ],
            [
                InlineKeyboardButton(text="⚖️ Согласия", callback_data=CALLBACK_CONSENTS_OPEN),
                InlineKeyboardButton(text="📄 Документы", callback_data="settings:docs"),
            ],
            [
                InlineKeyboardButton(text="👤 Профиль", callback_data="profile:refresh"),
                InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=CALLBACK_DELETE_OPEN),
            ],
        ]
    )


async def _consents_text(session: AsyncSession, db_user: User) -> str:
    service = ConsentService(session)
    active = await service.active(db_user.id)
    history = await service.history(db_user.id)
    lines = [
        "⚖️ <b>Согласия</b>",
        "",
        f"Редакция документов: {ui.esc(service.policy_version)}",
        "",
        "Обязательные:",
    ]
    for kind, label in (
        ("terms", "Соглашение"),
        ("privacy", "Политика конфиденциальности"),
        ("data_processing", "Обработка персональных данных"),
    ):
        row = active.get(kind)
        status = f"принято {row.granted_at:%d.%m.%Y}" if row else "не принято"
        lines.append(f"• {label}: {status}")
    lines.append("")
    lines.append("Добровольные (по умолчанию выключены):")
    for kind, label in (
        ("profile_publication", "Распространение данных анкеты"),
        ("marketing", "Рекламные уведомления"),
        ("image_advertising", "Изображение в рекламе"),
    ):
        row = active.get(kind)
        lines.append(f"• {label}: {'включено' if row else 'выключено'}")
    lines.append("")
    lines.append("Отзыв добровольного согласия действует сразу.")
    if history:
        lines.append(f"Всего записей в истории: {len(history)}")
    return "\n".join(lines)


async def _unpublish_public_profiles(session: AsyncSession, user_id) -> int:  # noqa: ANN001
    """Stop public distribution after the publication consent is revoked."""
    hidden = 0
    startups = (
        await session.scalars(
            select(Startup).where(
                Startup.owner_user_id == user_id,
                Startup.review_state == "approved",
                Startup.deleted_at.is_(None),
            )
        )
    ).all()
    for startup in startups:
        startup.review_state = "draft"
        hidden += 1
    investors = (
        await session.scalars(
            select(InvestorProfile).where(
                InvestorProfile.user_id == user_id,
                InvestorProfile.review_status == "approved",
                InvestorProfile.deleted_at.is_(None),
            )
        )
    ).all()
    for investor in investors:
        investor.review_status = "draft"
        hidden += 1
    await session.flush()
    return hidden


DATA_REVOKE_PROMPT = (
    "⚠️ <b>Отзыв согласия на обработку персональных данных</b>\n\n"
    "Если отозвать согласие, RAUNDO больше не сможет обрабатывать ваши данные "
    "для работы сервиса:\n"
    "• анкеты снимаются с публикации;\n"
    "• рекламные уведомления прекращаются;\n"
    "• разделы, где нужна обработка данных, станут недоступны, пока вы снова не дадите согласие.\n\n"
    "Мы сохраним только сведения, которые обязаны хранить по закону "
    "(например, платёжные записи).\n\n"
    "Если пользоваться сервисом больше не планируете - удалите аккаунт: "
    "персональные данные будут анонимизированы сразу.\n\n"
    "Поддержка и юридические обращения остаются доступны."
)

DATA_REVOKED_TEXT = (
    "✅ <b>Согласие на обработку ПД отозвано</b>\n\n"
    "Обработка прекращена, анкеты сняты с публикации, рекламные уведомления отключены.\n"
    "Разделы RAUNDO недоступны, пока вы не дадите согласие снова.\n\n"
    "Вы можете:\n"
    "• снова принять согласия (следующая команда в боте);\n"
    "• удалить аккаунт - данные будут анонимизированы.\n\n"
    "Поддержка: /support. Документы: /legal."
)


async def on_data_revoke_prompt(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(DATA_REVOKE_PROMPT, reply_markup=data_revoke_keyboard())
    except Exception:  # noqa: BLE001
        await callback.message.answer(DATA_REVOKE_PROMPT, reply_markup=data_revoke_keyboard())


async def on_data_revoke_confirm(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    service = ConsentService(session)
    try:
        await service.revoke_data_processing(db_user.id)
        await _unpublish_public_profiles(session, db_user.id)
        await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.recipient_user_id == db_user.id,
                OutboxEvent.state.in_(("pending", "retrying")),
            )
            .values(state="canceled")
        )
        await session.commit()
    except Exception:
        await session.rollback()
        await callback.answer("Не удалось отозвать согласие.", show_alert=True)
        return
    await callback.answer("Согласие отозвано")
    if callback.message is not None:
        await callback.message.answer(DATA_REVOKED_TEXT, reply_markup=main_menu())


async def on_data_revoke_cancel(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    await callback.answer("Согласие сохранено")
    await on_consents(callback, session, db_user)


async def on_consents(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    service = ConsentService(session)
    active = await service.active(db_user.id)
    flags = {kind: kind in active for kind in OPTIONAL_KINDS}
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(
            await _consents_text(session, db_user),
            reply_markup=consents_manage_keyboard(flags),
        )
    except Exception:  # noqa: BLE001
        await callback.message.answer(
            await _consents_text(session, db_user),
            reply_markup=consents_manage_keyboard(flags),
        )


async def on_consent_toggle(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    data = callback.data or ""
    kind = data[len(CALLBACK_CONSENT_REVOKE_PREFIX) :]
    if kind not in OPTIONAL_KINDS:
        await callback.answer("Неизвестное согласие.", show_alert=True)
        return
    service = ConsentService(session)
    active = await service.active(db_user.id)
    try:
        if kind in active:
            await service.revoke(db_user.id, kind, reason="user_request")
            extra = ""
            if kind == CONSENT_PROFILE_PUBLICATION:
                hidden = await _unpublish_public_profiles(session, db_user.id)
                extra = f" Скрыто профилей: {hidden}."
            await session.commit()
            await callback.answer(f"Согласие отозвано.{extra}", show_alert=bool(extra))
        else:
            await service.grant(db_user.id, kind, source="settings")
            await session.commit()
            await callback.answer("Согласие включено")
    except Exception:
        await session.rollback()
        await callback.answer("Не удалось изменить согласие.", show_alert=True)
        return
    active_after = await service.active(db_user.id)
    flags = {item: item in active_after for item in OPTIONAL_KINDS}
    if callback.message is not None:
        try:
            await callback.message.edit_text(
                await _consents_text(session, db_user),
                reply_markup=consents_manage_keyboard(flags),
            )
        except Exception:  # noqa: BLE001
            pass


async def profile_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user, get_settings(), state):
        return
    # Leaving another flow (feed, wizard) must release its reply keyboard.
    await state.clear()
    await message.answer(
        await _profile_text(session, db_user), reply_markup=main_menu()
    )


def _favorites_text(items) -> str:  # noqa: ANN001
    lines = ["🔖 <b>Избранное</b>", ""]
    if not items:
        lines.append("Пока пусто. Откройте «🚀 Оценивать» и сохраните проект кнопкой ⭐.")
        return "\n".join(lines)
    for startup in items:
        lines.append(f"• {ui.b(startup.name or 'Проект')}")
        if startup.one_liner:
            lines.append(f"  {ui.esc(_short(startup.one_liner))}")
    lines.append("")
    lines.append(f"Всего: {len(items)}")
    return "\n".join(lines)


def _short(text: str, limit: int = 120) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


async def _favorites(session: AsyncSession, user_id) -> list[Startup]:  # noqa: ANN001
    rows = await session.execute(
        select(Startup)
        .join(UserCollectionItem, UserCollectionItem.startup_id == Startup.id)
        .join(UserCollection, UserCollection.id == UserCollectionItem.collection_id)
        .where(
            UserCollection.user_id == user_id,
            Startup.deleted_at.is_(None),
            Startup.review_state == "approved",
        )
        .order_by(UserCollectionItem.created_at.desc())
        .limit(20)
    )
    return list(rows.scalars())


async def favorites_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user, get_settings(), state):
        return
    await state.clear()
    items = await _favorites(session, db_user.id)
    await message.answer(_favorites_text(items), reply_markup=main_menu())


async def on_favorites(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    items = await _favorites(session, db_user.id)
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(_favorites_text(items), reply_markup=_profile_keyboard())
    except Exception:  # noqa: BLE001
        await callback.message.answer(_favorites_text(items), reply_markup=_profile_keyboard())


async def settings_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user, get_settings(), state):
        return
    await _profile_row(session, db_user.id)
    await session.commit()
    await message.answer(
        await _settings_text(session, db_user),
        reply_markup=main_menu(),
    )


async def on_settings(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    profile = await _profile_row(session, db_user.id)
    await session.commit()
    text = await _settings_text(session, db_user)
    keyboard = _settings_keyboard(profile, await _notification_enabled(session, db_user.id))
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:  # noqa: BLE001
        await callback.message.answer(text, reply_markup=keyboard)


async def on_setting_toggle(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    data = callback.data or ""
    parts = data.split(":")
    profile = await _profile_row(session, db_user.id)
    if len(parts) == 4 and parts[1] == "contact":
        mode = parts[3]
        if mode not in {"relay", "username"}:
            await callback.answer("Неизвестный вариант.", show_alert=True)
            return
        if mode == "username" and not db_user.username:
            await callback.answer(
                "У вас нет Telegram username. Выберите связь через бота.",
                show_alert=True,
            )
            return
        profile.contact_mode = mode
        profile.contact_username = db_user.username if mode == "username" else None
    elif len(parts) == 4 and parts[1] == "reveal":
        profile.show_contact_after_accept = parts[3] == "on"
    elif len(parts) == 4 and parts[1] == "notify":
        pref = await CommunityPlusService(session).notification_pref(db_user.id)
        pref.enabled = parts[3] == "on"
    else:
        await callback.answer()
        return
    await session.commit()
    await callback.answer("Сохранено")
    await on_settings(callback, session, db_user)


async def on_profile(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    text = await _profile_text(session, db_user)
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=_profile_keyboard())
    except Exception:  # noqa: BLE001
        await callback.message.answer(text, reply_markup=_profile_keyboard())


async def on_profile_premium(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    subs = await PremiumService(session).my_subscriptions(db_user.id)
    if not subs:
        text = "⭐ Premium\n\nАктивных подписок нет. Каталог тарифов: /premium."
    else:
        lines = ["⭐ <b>Мои подписки</b>"]
        for s in subs:
            lines.append(
                f"• {ui.plan_title(s.plan_code)} до {ui.date(s.expires_at)}"
                + (" · тестовая" if s.is_test else "")
            )
        text = "\n".join(lines)
    if callback.message is not None:
        await callback.message.answer(text, reply_markup=main_menu())


async def on_docs(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "Документы: /terms · Условия использования, /privacy · Политика "
            "конфиденциальности. Согласие можно принять через /start.",
            reply_markup=main_menu(),
        )


def _requests_text(incoming, outgoing) -> str:  # noqa: ANN001
    lines = ["🤝 <b>Запросы на знакомство</b>", ""]
    if not incoming and not outgoing:
        lines.append("Пока пусто.")
        lines.append("")
        lines.append("Отправьте запрос из ленты: откройте проект и нажмите «🤝 Запрос на знакомство».")
        return "\n".join(lines)

    lines.append("<b>Входящие</b>")
    if not incoming:
        lines.append("• нет")
    for request, startup, requester in incoming:
        who = " ".join(
            part
            for part in (
                getattr(requester, "first_name", None),
                getattr(requester, "last_name", None),
            )
            if part
        )
        if requester is not None and requester.username:
            who = f"{who} (@{requester.username})" if who else f"@{requester.username}"
        lines.append(
            f"• «{escape(startup.name or 'Проект')}» · {ui.request_status(request.status)}"
        )
        if request.message:
            lines.append(f"  {escape(_short(request.message, 200))}")
        if who:
            lines.append(f"  От: {escape(who)}")

    lines.append("")
    lines.append("<b>Исходящие</b>")
    if not outgoing:
        lines.append("• нет")
    for request, startup in outgoing:
        lines.append(
            f"• «{escape(startup.name or 'Проект')}» · {ui.request_status(request.status)}"
        )
    return ui.truncate("\n".join(lines))


def _requests_keyboard(rows) -> InlineKeyboardMarkup:  # noqa: ANN001
    buttons: list[list[InlineKeyboardButton]] = []
    for request, startup, _requester in rows:
        if request.status != REQUEST_PENDING:
            continue
        label = _short(startup.name or "Проект", 22)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"✅ {label}", callback_data=f"cr:accept:{request.id}"
                ),
                InlineKeyboardButton(
                    text=f"❌ {label}", callback_data=f"cr:decline:{request.id}"
                ),
            ]
        )
    buttons.append([InlineKeyboardButton(text="👤 Профиль", callback_data="profile:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def requests_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext | None = None
) -> None:
    if state is not None:
        await state.clear()
    incoming = await contacts.requests_for_owner(session, db_user.id)
    outgoing = await contacts.requests_for_requester(session, db_user.id)
    await message.answer(
        _requests_text(incoming, outgoing),
        reply_markup=main_menu(),
    )
    if incoming:
        await message.answer(
            "Решения по входящим запросам:",
            reply_markup=_requests_keyboard(incoming),
        )


async def on_requests(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    incoming = await contacts.requests_for_owner(session, db_user.id)
    outgoing = await contacts.requests_for_requester(session, db_user.id)
    text = _requests_text(incoming, outgoing)
    keyboard = _requests_keyboard(incoming)
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:  # noqa: BLE001
        await callback.message.answer(text, reply_markup=keyboard)


async def on_request_decision(
    callback: CallbackQuery, session: AsyncSession, db_user: User
) -> None:
    data = callback.data or ""
    try:
        _, action, request_id = data.split(":", 2)
        request_uuid = uuid.UUID(request_id)
    except (ValueError, TypeError):
        await callback.answer("Кнопка устарела. Откройте «🤝 Запросы» заново.", show_alert=True)
        return
    accept = action == "accept"
    try:
        await contacts.decide(
            session,
            owner_id=db_user.id,
            request_id=request_uuid,
            accept=accept,
        )
        await session.commit()
    except ContactError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception:  # noqa: BLE001
        await session.rollback()
        await callback.answer(
            "Не удалось выполнить действие. Попробуйте ещё раз.", show_alert=True
        )
        return
    await callback.answer("Принято" if accept else "Отклонено")
    incoming = await contacts.requests_for_owner(session, db_user.id)
    outgoing = await contacts.requests_for_requester(session, db_user.id)
    if callback.message is not None:
        try:
            await callback.message.edit_text(
                _requests_text(incoming, outgoing),
                reply_markup=_requests_keyboard(incoming),
            )
        except Exception:  # noqa: BLE001
            await callback.message.answer(
                _requests_text(incoming, outgoing),
                reply_markup=_requests_keyboard(incoming),
            )


def create_router() -> Router:
    router = Router(name="profile")
    router.message.register(profile_command, Command("profile"))
    router.message.register(profile_command, F.text == MENU_PROFILE)
    router.message.register(settings_command, Command("settings"))
    router.message.register(settings_command, F.text == MENU_SETTINGS)
    router.message.register(requests_command, Command("requests"))
    router.message.register(requests_command, F.text == MENU_REQUESTS)
    router.message.register(favorites_command, Command("favorites"))
    router.message.register(favorites_command, F.text == MENU_FAVORITES)
    router.callback_query.register(on_profile, F.data.in_({"profile:refresh", "profile:profile"}))
    router.callback_query.register(on_favorites, F.data == "profile:favorites")
    router.callback_query.register(on_profile_premium, F.data == "profile:premium")
    router.callback_query.register(on_settings, F.data == "profile:settings")
    router.callback_query.register(
        on_docs, F.data == "settings:docs"
    )
    router.callback_query.register(
        on_setting_toggle, F.data.startswith("settings:contact:")
    )
    router.callback_query.register(
        on_setting_toggle, F.data.startswith("settings:reveal:")
    )
    router.callback_query.register(
        on_setting_toggle, F.data.startswith("settings:notify:")
    )
    router.callback_query.register(
        on_data_revoke_prompt, F.data == "consent:revoke:data_processing"
    )
    router.callback_query.register(on_data_revoke_confirm, F.data == "consent:data:confirm")
    router.callback_query.register(on_data_revoke_cancel, F.data == "consent:data:cancel")
    router.callback_query.register(on_consents, F.data == CALLBACK_CONSENTS_OPEN)
    router.callback_query.register(
        on_consent_toggle, F.data.startswith(CALLBACK_CONSENT_REVOKE_PREFIX)
    )
    router.callback_query.register(on_requests, F.data == "profile:requests")
    router.callback_query.register(
        on_request_decision, F.data.startswith("cr:accept:")
    )
    router.callback_query.register(
        on_request_decision, F.data.startswith("cr:decline:")
    )
    return router
