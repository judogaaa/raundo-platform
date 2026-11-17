""" /rate feed: native reply keyboard, one card at a time, safe state.

Approved UX:
  ❤️ | 👎
  ⭐ | 🏆 Топ-10
  Подробнее
  🤝 Запрос на знакомство

The current startup is stored in FSM state (Redis), never inferred from chat
text. Old inline buttons are still handled for message history compatibility but
new cards never render the big inline grid.
"""

from __future__ import annotations

import time
import uuid

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.config import get_settings
from raundo.keyboards import MENU_RATE, MENU_TOP, main_menu
from raundo.models.startup import Startup, StartupSector
from raundo.models.user import User
from raundo.routers.common import _require_consent
from raundo.services import contact_requests as contacts
from raundo.services import feed as feed_service
from raundo.services.contact_requests import ContactError
from raundo.services.feed import FeedError

BTN_LIKE = "❤️"
BTN_DISLIKE = "👎"
BTN_FAVORITE = "⭐"
BTN_DETAILS = "Подробнее"
BTN_TOP = MENU_TOP
BTN_CONTACT = "🤝 Запрос на знакомство"
BTN_CANCEL = "❌ Отмена"

FEED_ACTIONS = {
    BTN_LIKE,
    BTN_DISLIKE,
    BTN_FAVORITE,
    BTN_DETAILS,
    BTN_TOP,
    BTN_CONTACT,
}

# Protects against accidental double taps: a repeated feed action within this
# window is ignored, so the next card can never be rated by the second tap.
ACTION_COOLDOWN_SECONDS = 1.0

_SECTOR_NAMES = {
    "ai_ml": "AI/ML",
    "saas_b2b": "SaaS/B2B",
    "fintech": "FinTech",
    "healthtech": "HealthTech",
    "edtech": "EdTech",
    "ecommerce": "E-commerce",
    "marketplace": "Marketplace",
    "industry_robotics": "Industry/Robotics",
    "mobility": "Mobility",
    "climate_energy": "Climate/Energy",
    "consumer": "Consumer",
    "gaming": "Gaming",
    "proptech": "PropTech",
    "security": "Security",
    "media_creator": "Media/Creator",
    "other": "Другое",
}


class FeedFlow(StatesGroup):
    browsing = State()


class ContactFlow(StatesGroup):
    message = State()


def _feed_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LIKE), KeyboardButton(text=BTN_DISLIKE)],
            [KeyboardButton(text=BTN_FAVORITE), KeyboardButton(text=BTN_TOP)],
            [KeyboardButton(text=BTN_DETAILS)],
            [KeyboardButton(text=BTN_CONTACT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Оценивайте стартапы",
    )


async def _startup_meta(session: AsyncSession, startup: Startup) -> tuple[str, str]:
    sector = ""
    stage = ""
    if startup.primary_sector_id:
        rows = await session.execute(
            select(StartupSector.sector_id).where(StartupSector.startup_id == startup.id)
        )
        names = [_SECTOR_NAMES.get(str(sid)) for sid in rows.scalars()]
        names = [name for name in names if name]
        sector = ", ".join(names)
    if startup.stage_id:
        from raundo.repositories.catalog import CatalogRepository

        for item in await CatalogRepository(session).list_stages():
            if item.id == startup.stage_id:
                stage = item.label
                break
    return sector, stage


def _photo_source(file_id: str | None):
    if not file_id:
        return None
    from raundo.services.media_proxy import _demo_media_path

    demo_path = _demo_media_path(file_id)
    if demo_path is not None:
        return FSInputFile(str(demo_path))
    if file_id.startswith("demo:"):
        return None
    return file_id


def _short_pitch(startup: Startup) -> str:
    text = (startup.one_liner or "").strip()
    if len(text) <= 160:
        return text
    return text[:157].rstrip() + "…"


def _card_text(startup: Startup, stage: str, country: str, ask: str) -> str:
    lines = [f"🚀 {ui.b(startup.name or 'Проект')}", ui.esc(_short_pitch(startup))]
    meta = [part for part in (stage, country) if part]
    if meta:
        lines.append(ui.esc(" · ".join(meta)))
    if ask:
        lines.append(f"Инвестиции: {ui.esc(ask)}")
    return ui.truncate("\n".join(lines))


async def _load_current(
    session: AsyncSession, state: FSMContext, user_id: uuid.UUID
) -> tuple[Startup | None, str | None]:
    data = await state.get_data()
    raw = data.get("feed_startup_id")
    if not raw:
        return None, None
    startup = await session.get(Startup, uuid.UUID(str(raw)))
    if (
        startup is None
        or startup.deleted_at is not None
        or startup.review_state != "approved"
        or startup.published_at is None
        or startup.owner_user_id == user_id
    ):
        return None, str(raw)
    return startup, str(raw)


async def _delete_previous(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    previous = data.get("feed_card_message_id")
    if not previous:
        return
    try:
        await message.bot.delete_message(message.chat.id, int(previous))
    except Exception:  # noqa: BLE001 - message may already be gone or too old
        pass
    await state.update_data(feed_card_message_id=None)


async def _send_card(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    startup: Startup | None,
) -> None:
    await _delete_previous(message, state)
    if startup is None:
        # No rate keyboard when there is nothing to rate: it would promise an
        # action that cannot work. The menu gives a clear way back or retry.
        await state.set_state(None)
        await state.update_data(feed_startup_id=None, feed_card_message_id=None)
        await message.answer(
            "Пока новых стартапов нет.\n\n"
            "Зайдите позже: кнопка «🚀 Оценивать» покажет новые проекты. "
            "Рейтинг доступен в разделе «🏆 Топ-10».",
            reply_markup=main_menu(),
        )
        return
    await state.set_state(FeedFlow.browsing)
    await state.update_data(feed_startup_id=str(startup.id))
    sector, stage = await _startup_meta(session, startup)
    ask = ui.money(startup.ask_amount_minor, startup.ask_currency)
    if ask == "Не указано":
        ask = ""
    text = _card_text(startup, stage, startup.country or "", ask)
    file_id = await feed_service.first_media_file_id(session, startup.id)
    source = _photo_source(file_id)
    if source is not None:
        sent = await message.answer_photo(
            source, caption=text, reply_markup=_feed_keyboard()
        )
    else:
        sent = await message.answer(text, reply_markup=_feed_keyboard())
    await state.update_data(feed_card_message_id=sent.message_id)


async def _show_next(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    startup, _ = await feed_service.next_feed_startup(session, user_id=user.id)
    await _send_card(message, session, user, state, startup)


async def _mark_action(state: FSMContext, action: str) -> bool:
    """Return False for a repeated tap inside the cooldown window."""
    data = await state.get_data()
    now = time.monotonic()
    last_at = float(data.get("feed_action_at") or 0)
    last_action = str(data.get("feed_last_action") or "")
    if last_action and now - last_at < ACTION_COOLDOWN_SECONDS:
        return False
    await state.update_data(feed_action_at=now, feed_last_action=action)
    return True


async def rate_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user, get_settings(), state):
        return
    data = await state.get_data()
    intro_seen = bool(data.get("feed_intro_seen"))
    await state.update_data(feed_intro_seen=True)
    if not intro_seen:
        await message.answer(
            "🚀 <b>Оценка стартапов</b>\n\n"
            "Здесь можно оценивать стартапы одним касанием: ❤️ нравится, 👎 не интересно. "
            "⭐ сохранит проект в избранное. После оценки откроется следующий.",
            reply_markup=_feed_keyboard(),
        )
        await state.update_data(feed_card_message_id=None)
    startup, _ = await feed_service.next_feed_startup(session, user_id=db_user.id)
    await _send_card(message, session, db_user, state, startup)


async def _current_or_next(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> Startup | None:
    startup, _stale_id = await _load_current(session, state, user.id)
    if startup is not None:
        return startup
    await message.answer(
        "Этот проект больше недоступен. Покажу следующий.",
        reply_markup=_feed_keyboard(),
    )
    return None


async def on_like(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    if not await _mark_action(state, "like"):
        return
    startup = await _current_or_next(message, session, db_user, state)
    if startup is None:
        await _show_next(message, session, db_user, state)
        return
    try:
        await feed_service.react(
            session, user_id=db_user.id, startup_id=startup.id, kind="like"
        )
        await session.commit()
    except FeedError as exc:
        await session.rollback()
        await message.answer(str(exc), reply_markup=_feed_keyboard())
    await _show_next(message, session, db_user, state)


async def on_dislike(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _mark_action(state, "dislike"):
        return
    startup = await _current_or_next(message, session, db_user, state)
    if startup is None:
        await _show_next(message, session, db_user, state)
        return
    try:
        await feed_service.react(
            session, user_id=db_user.id, startup_id=startup.id, kind="dislike"
        )
        await session.commit()
    except FeedError as exc:
        await session.rollback()
        await message.answer(str(exc), reply_markup=_feed_keyboard())
    await _show_next(message, session, db_user, state)


async def on_skip(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    if not await _mark_action(state, "skip"):
        return
    startup = await _current_or_next(message, session, db_user, state)
    if startup is not None:
        try:
            await feed_service.react(
                session, user_id=db_user.id, startup_id=startup.id, kind="skip"
            )
            await session.commit()
        except FeedError:
            await session.rollback()
    await _show_next(message, session, db_user, state)


async def on_favorite(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _current_or_next(message, session, db_user, state)
    if startup is None:
        return
    try:
        added = await feed_service.toggle_favorite(
            session, user_id=db_user.id, startup_id=startup.id
        )
        await session.commit()
    except FeedError as exc:
        await session.rollback()
        await message.answer(str(exc), reply_markup=_feed_keyboard())
        return
    await message.answer(
        "⭐ Добавлено в избранное." if added else "Убрано из избранного.",
        reply_markup=_feed_keyboard(),
    )


async def on_details(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _current_or_next(message, session, db_user, state)
    if startup is None:
        return
    sector, stage = await _startup_meta(session, startup)
    parts = [f"🚀 {ui.b(startup.name or 'Проект')}"]
    pitch = _short_pitch(startup)
    if pitch:
        parts.append(ui.esc(pitch))
    if startup.full_description:
        parts.append("")
        parts.append("<b>О проекте</b>")
        parts.append(ui.esc(startup.full_description))
    meta = [part for part in (sector, stage) if part]
    lines: list[str] = []
    if meta:
        lines.append(f"<b>Отрасль и стадия</b>: {ui.esc(' · '.join(meta))}")
    if startup.country:
        lines.append(f"<b>География</b>: {ui.esc(startup.country)}")
    if startup.monetization_status:
        monetization = ui.monetization(startup.monetization_status)
        if startup.monetization_note:
            monetization = f"{monetization}. {startup.monetization_note}"
        lines.append(f"<b>Монетизация</b>: {ui.esc(monetization)}")
    money = ui.money(startup.ask_amount_minor, startup.ask_currency)
    if money != "Не указано":
        lines.append(f"<b>Инвестиции</b>: {ui.esc(money)}")
    if startup.fundraising_stage:
        lines.append(f"<b>Раунд</b>: {ui.esc(_fundraising_label(startup.fundraising_stage))}")
    goals = _intent_labels(startup.intents)
    if goals:
        lines.append(f"<b>Ищет</b>: {ui.esc(goals)}")
    if startup.founder_display_name:
        team = startup.founder_display_name
        if startup.founder_role:
            team = f"{team}, {startup.founder_role}"
        lines.append(f"<b>Команда</b>: {ui.esc(team)}")
    links = []
    for url, label in (
        (startup.website_url, "Сайт"),
        (startup.app_url, "Приложение"),
        (startup.demo_url, "Демо"),
    ):
        if url:
            links.append(f"{label}: {ui.link(url, url)}")
    if links:
        lines.append("<b>Ссылки</b>")
        lines.extend(links)
    if lines:
        parts.append("")
        parts.append("\n".join(lines))
    media = await _media_list(session, startup.id)
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if len(media) > 1:
        keyboard_rows.append(
            [
                InlineKeyboardButton(text=str(index + 1), callback_data=f"feed:gal:{startup.id}:{index}")
                for index in range(min(len(media), 5))
            ]
        )
    keyboard_rows.append(
        [InlineKeyboardButton(text="← К оценке", callback_data="feed:back")]
    )
    await message.answer(
        ui.truncate("\n".join(parts)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


def _fundraising_label(code: str) -> str:
    from raundo.domain import STARTUP_FUNDRAISING_LABELS

    return STARTUP_FUNDRAISING_LABELS.get(code, "")


def _intent_labels(codes) -> str:  # noqa: ANN001
    from raundo.domain import STARTUP_INTENT_LABELS

    labels = [STARTUP_INTENT_LABELS.get(str(code), "") for code in (codes or [])]
    return ", ".join(label for label in labels if label)


async def _media_list(session: AsyncSession, startup_id: uuid.UUID) -> list[str]:
    from raundo.models.startup import StartupMedia

    rows = await session.execute(
        select(StartupMedia.telegram_file_id)
        .where(StartupMedia.startup_id == startup_id)
        .order_by(StartupMedia.position)
    )
    return [file_id for file_id in rows.scalars() if file_id]


async def on_feed_top(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await message.answer(await _top_text(session, "community"), reply_markup=_top_keyboard())


async def _top_text(session: AsyncSession, cohort: str) -> str:
    period, items = await feed_service.top_startups(session, cohort=cohort)
    title = "🏆 Топ-10 сообщества" if cohort == "community" else "🏆 Топ-10 инвесторов"
    lines = [
        f"<b>{title}</b>",
        f"Период: {period.start:%d.%m.%Y} · {period.end:%d.%m.%Y} (UTC)",
        "",
    ]
    if not items:
        lines.append("Пока мало оценок для честного рейтинга. Загляните позже.")
        return "\n".join(lines)
    for item in items:
        startup: Startup = item["startup"]
        _, stage = await _startup_meta(session, startup)
        meta = f" · {ui.esc(stage)}" if stage else ""
        lines.append(
            f"{item['position']}. <b>{ui.esc(startup.name or 'Проект')}</b>{meta} · ❤️ {item['likes']}"
        )
    if len(items) < 10:
        lines.append("")
        lines.append(f"Проектов с оценками пока {len(items)} из 10.")
    lines.append("")
    lines.append("Учитываются уникальные оценки за период. Premium на рейтинг не влияет.")
    return "\n".join(lines)


def _top_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сообщество", callback_data="top:community"),
                InlineKeyboardButton(text="Инвесторы", callback_data="top:verified"),
            ],
            [InlineKeyboardButton(text="← К оценке", callback_data="feed:back")],
        ]
    )


async def top_command(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    if not await _require_consent(message, session, db_user, get_settings(), state):
        return
    await message.answer(await _top_text(session, "community"), reply_markup=_top_keyboard())


async def on_top_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    cohort = "verified" if (callback.data or "") == "top:verified" else "community"
    key = "verified_investors" if cohort == "verified" else "community"
    text = await _top_text(session, key)
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=_top_keyboard())
        except Exception:  # noqa: BLE001
            await callback.message.answer(text, reply_markup=_top_keyboard())


async def on_back_to_feed(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await callback.answer()
    startup, _ = await _load_current(session, state, db_user.id)
    if startup is None:
        startup, _ = await feed_service.next_feed_startup(session, user_id=db_user.id)
    if callback.message is not None:
        await _send_card(callback.message, session, db_user, state, startup)


async def on_gallery(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    try:
        if len(parts) != 4:
            raise ValueError("bad callback")
        startup_id = uuid.UUID(parts[2])
        index = int(parts[3])
    except (ValueError, TypeError):
        await callback.answer("Кнопка устарела. Откройте «Подробнее» заново.", show_alert=True)
        return
    media = await _media_list(session, startup_id)
    if index < 0 or index >= len(media):
        await callback.answer("Фото больше недоступно.", show_alert=True)
        return
    source = _photo_source(media[index])
    if source is None:
        await callback.answer("Фото недоступно.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer_photo(source)


async def on_contact_prompt(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    startup = await _current_or_next(message, session, db_user, state)
    if startup is None:
        return
    await state.set_state(ContactFlow.message)
    await state.update_data(contact_startup_id=str(startup.id))
    await message.answer(
        f"🤝 Запрос на знакомство: «{ui.esc(startup.name or 'Проект')}».\n\n"
        "Напишите одно сообщение основателю: кто вы и зачем хотите связаться. "
        "Контакты раскрываются только после согласия обеих сторон.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
            resize_keyboard=True,
            one_time_keyboard=False,
        ),
    )


async def on_contact_cancel(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await state.set_state(FeedFlow.browsing)
    await state.update_data(contact_startup_id=None)
    await message.answer("Запрос отменён.", reply_markup=_feed_keyboard())


async def on_contact_not_text(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await message.answer(
        "Нужен текст: напишите пару слов о себе или нажмите «❌ Отмена».",
    )


async def on_contact_message(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    data = await state.get_data()
    startup_id = data.get("contact_startup_id") or data.get("feed_startup_id")
    if not startup_id:
        await state.set_state(FeedFlow.browsing)
        await message.answer("Запрос не найден. Откройте ленту: /rate.", reply_markup=main_menu())
        return
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer(
            "Добавьте пару слов о себе: минимум 10 символов, или нажмите «❌ Отмена»."
        )
        return
    try:
        await contacts.create_request(
            session,
            requester_id=db_user.id,
            startup_id=uuid.UUID(str(startup_id)),
            message=text,
        )
        await session.commit()
    except ContactError as exc:
        await session.rollback()
        await state.set_state(FeedFlow.browsing)
        await state.update_data(contact_startup_id=None)
        await message.answer(str(exc), reply_markup=_feed_keyboard())
        return
    await state.set_state(FeedFlow.browsing)
    await state.update_data(contact_startup_id=None)
    await message.answer(
        "✅ Запрос отправлен.\n\n"
        "Основатель сможет принять или отклонить его. Статус виден в разделе «🤝 Запросы».",
        reply_markup=_feed_keyboard(),
    )


# ── Legacy inline callbacks (old cards in chat history) ──────────────────────


async def _legacy_action(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext, kind: str
) -> None:
    _, _, sid = (callback.data or "").split(":", 2)
    startup = await session.get(Startup, uuid.UUID(sid))
    if startup is None or startup.review_state != "approved" or startup.deleted_at is not None:
        await callback.answer("Проект недоступен.", show_alert=True)
        return
    try:
        await feed_service.react(session, user_id=db_user.id, startup_id=startup.id, kind=kind)
        await session.commit()
    except FeedError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _send_card(callback.message, session, db_user, state, startup)


def create_router() -> Router:
    router = Router(name="feed")
    router.message.register(rate_command, Command("rate"))
    router.message.register(rate_command, F.text == MENU_RATE)
    router.message.register(top_command, Command("top"))

    browsing = StateFilter(FeedFlow.browsing)
    router.message.register(on_like, browsing, F.text == BTN_LIKE)
    router.message.register(on_dislike, browsing, F.text == BTN_DISLIKE)
    router.message.register(on_favorite, browsing, F.text == BTN_FAVORITE)
    router.message.register(on_details, browsing, F.text == BTN_DETAILS)
    router.message.register(on_feed_top, browsing, F.text == MENU_TOP)
    router.message.register(top_command, F.text == MENU_TOP)
    router.message.register(on_contact_prompt, browsing, F.text == BTN_CONTACT)

    router.callback_query.register(on_top_callback, F.data.in_({"top:community", "top:verified"}))
    router.callback_query.register(on_back_to_feed, F.data == "feed:back")
    router.callback_query.register(on_gallery, F.data.startswith("feed:gal:"))
    async def on_legacy_like(cb, session, db_user, state):
        await _legacy_action(cb, session, db_user, state, "like")

    async def on_legacy_dislike(cb, session, db_user, state):
        await _legacy_action(cb, session, db_user, state, "dislike")

    async def on_legacy_stale(cb, session, db_user, state):
        await cb.answer("Кнопка устарела. Откройте /rate.", show_alert=True)

    router.callback_query.register(on_legacy_like, F.data.startswith("rate:like:"))
    router.callback_query.register(on_legacy_dislike, F.data.startswith("rate:dislike:"))
    router.callback_query.register(on_back_to_feed, F.data == "rate:next")
    router.callback_query.register(on_legacy_stale, F.data.startswith("rate:"))
    router.message.register(
        on_contact_cancel,
        ContactFlow.message,
        F.text == BTN_CANCEL,
    )
    router.message.register(
        on_contact_message,
        ContactFlow.message,
        F.text & ~F.text.startswith("/"),
    )
    router.message.register(on_contact_not_text, ContactFlow.message)
    return router
