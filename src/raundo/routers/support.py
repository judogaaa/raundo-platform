"""Private support tickets inside @RaundoAppBot. Staff replies come from the web admin."""

from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.keyboards import MENU_SUPPORT, main_menu
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.user import User
from raundo.services.support_tickets import (
    CATEGORIES,
    Attachment,
    SupportError,
    add_system_message,
    add_user_message,
    confirm_resolution,
    create_ticket,
    reject_resolution,
)


class SupportFlow(StatesGroup):
    category = State()
    subject = State()
    body = State()
    pick_ticket = State()
    reply_body = State()
    dispute = State()
    choose_ticket = State()


CB_CAT = "sup:cat:"
CB_LIST = "sup:list"
CB_OPEN = "sup:open:"
CB_REPLY = "sup:reply:"
CB_NEW = "sup:new"
CB_YES = "sv:yes:"
CB_NO = "sv:no:"
CB_PICK = "sv:pick:"


def _categories_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{CB_CAT}{code}")]
        for code, label in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton(text="📂 Мои обращения", callback_data=CB_LIST)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_support(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportFlow.category)
    await message.answer(
        "🆘 <b>Поддержка RAUNDO</b>\n\n"
        "Опишите вопрос, и мы ответим в этом чате. "
        "Историю обращений можно открыть в любой момент.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Выберите тему обращения:", reply_markup=_categories_keyboard()
    )


def _pick_category(text: str) -> str | None:
    clean = (text or "").strip().lower()
    if clean.isdigit():
        codes = list(CATEGORIES.keys())
        index = int(clean) - 1
        if 0 <= index < len(codes):
            return codes[index]
        return None
    for code, label in CATEGORIES.items():
        if code in clean or label.lower() in clean:
            return code
    return None


async def on_category(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    text = (message.text or "").strip().lower()
    if text in {"отмена", "/cancel"}:
        await state.clear()
        await message.answer("Обращение отменено.", reply_markup=main_menu())
        return
    if text in {"мои обращения", "обращения"}:
        await state.set_state(SupportFlow.pick_ticket)
        await _list_tickets(message, session, db_user, state)
        return
    code = _pick_category(text)
    if code is None:
        await message.answer(
            "Выберите тему кнопкой в списке выше или отправьте номер категории."
        )
        return
    await _ask_subject(message, state, code)


async def _ask_subject(message: Message, state: FSMContext, code: str) -> None:
    await state.update_data(category=code)
    await state.set_state(SupportFlow.subject)
    await message.answer(
        f"Тема: {CATEGORIES.get(code, 'обращение')}.\n"
        "Кратко напишите, с чем нужна помощь."
    )


async def on_category_cb(callback: CallbackQuery, state: FSMContext) -> None:
    data = callback.data or ""
    if data == CB_NEW:
        await callback.answer()
        await state.set_state(SupportFlow.category)
        if callback.message is not None:
            await callback.message.answer(
                "Выберите тему обращения:", reply_markup=_categories_keyboard()
            )
        return
    code = data[len(CB_CAT) :] if data.startswith(CB_CAT) else ""
    if code not in CATEGORIES:
        await callback.answer("Кнопка устарела. Откройте /support.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _ask_subject(callback.message, state, code)


async def on_subject(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Тема слишком короткая. Напишите хотя бы несколько слов.")
        return
    await state.update_data(subject=text[:160])
    await state.set_state(SupportFlow.body)
    await message.answer(
        "Опишите ситуацию одним сообщением. Можно приложить фото или документ до 5 МБ."
    )


async def on_body(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if data.get("reply_ticket_id"):
        await _save_user_reply(message, session, db_user, state)
        return
    attachment = Attachment.from_telegram(message)
    try:
        ticket = await create_ticket(
            session,
            user_id=db_user.id,
            subject=data.get("subject") or "Обращение",
            body=message.text or message.caption or "",
            category=data.get("category"),
            attachment=attachment,
        )
    except SupportError as exc:
        await message.answer(exc.message)
        return
    await session.flush()
    await state.clear()
    await message.answer(
        f"✅ Обращение {ui.b(ticket.public_number)} принято.\n\n"
        "Ответ придёт в этот чат. Статус и переписка доступны в «🆘 Поддержка» → "
        "«Мои обращения».",
        reply_markup=main_menu(),
    )


async def _recent_tickets(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 10
) -> list[SupportTicket]:
    rows = await session.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.updated_at.desc())
        .limit(limit)
    )
    return list(rows.scalars())


def _tickets_keyboard(tickets: list[SupportTicket]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{ticket.public_number} · {ui.ticket_status(ticket.status)}",
                callback_data=f"{CB_OPEN}{ticket.id}",
            )
        ]
        for ticket in tickets
    ]
    rows.append([InlineKeyboardButton(text="➕ Новое обращение", callback_data=CB_NEW)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _tickets_text(tickets: list[SupportTicket]) -> str:
    if not tickets:
        return (
            "📂 <b>Мои обращения</b>\n\n"
            "Обращений пока нет. Создайте новое, если нужна помощь."
        )
    lines = ["📂 <b>Мои обращения</b>", ""]
    for ticket in tickets:
        lines.append(
            f"• {ui.b(ticket.public_number)} · {ui.esc(ui.ticket_status(ticket.status))}\n"
            f"  {ui.esc(ticket.subject or 'Обращение')}"
        )
    lines.append("")
    lines.append("Откройте обращение, чтобы прочитать переписку и ответить.")
    return ui.truncate("\n".join(lines))


async def _list_tickets(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    tickets = await _recent_tickets(session, db_user.id)
    await state.set_state(SupportFlow.pick_ticket)
    await message.answer(
        _tickets_text(tickets), reply_markup=_tickets_keyboard(tickets)
    )


async def _ticket_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    ticket: SupportTicket,
) -> None:
    rows = await session.execute(
        select(SupportMessage)
        .where(SupportMessage.ticket_id == ticket.id)
        .order_by(SupportMessage.created_at.desc())
        .limit(5)
    )
    messages = list(reversed(list(rows.scalars())))
    lines = [
        f"📄 {ui.b(ticket.public_number)} · {ui.esc(ui.ticket_status(ticket.status))}",
        ui.esc(ticket.subject or "Обращение"),
        "",
    ]
    for item in messages:
        author = {
            "user": "Вы",
            "staff": "Поддержка",
            "system": "Система",
        }.get(item.author_kind, "Поддержка")
        lines.append(f"<b>{author}</b>: {ui.esc(_clip(item.body, 300))}")
        if item.attachment_kind:
            kind = "фото" if item.attachment_kind == "photo" else "документ"
            lines.append(f"  Вложение: {kind}")
        lines.append("")
    if ticket.status == "closed":
        lines.append("Обращение закрыто. Можно создать новое.")
        buttons = [[InlineKeyboardButton(text="➕ Новое обращение", callback_data=CB_NEW)]]
    else:
        buttons = [
            [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"{CB_REPLY}{ticket.id}")],
            [InlineKeyboardButton(text="➕ Новое обращение", callback_data=CB_NEW)],
        ]
    buttons.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=CB_LIST)])
    await state.set_state(SupportFlow.pick_ticket)
    if callback.message is not None:
        await callback.message.answer(
            ui.truncate("\n".join(line for line in lines if line is not None)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


def _clip(text: str | None, limit: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


async def _owned_ticket(
    session: AsyncSession, user_id: uuid.UUID, raw_id: str
) -> SupportTicket | None:
    try:
        ticket_uuid = uuid.UUID(raw_id)
    except (ValueError, TypeError):
        return None
    ticket = await session.get(SupportTicket, ticket_uuid)
    if ticket is None or ticket.user_id != user_id:
        return None
    return ticket


async def on_open_ticket(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    ticket = await _owned_ticket(session, db_user.id, (callback.data or "")[len(CB_OPEN) :])
    if ticket is None:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return
    await callback.answer()
    await _ticket_detail(callback, session, db_user, state, ticket)


async def on_list_tickets(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    tickets = await _recent_tickets(session, db_user.id)
    await state.set_state(SupportFlow.pick_ticket)
    try:
        await callback.message.edit_text(
            _tickets_text(tickets), reply_markup=_tickets_keyboard(tickets)
        )
    except Exception:  # noqa: BLE001
        await callback.message.answer(
            _tickets_text(tickets), reply_markup=_tickets_keyboard(tickets)
        )


async def on_reply_ticket(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    ticket = await _owned_ticket(session, db_user.id, (callback.data or "")[len(CB_REPLY) :])
    if ticket is None:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return
    if ticket.status == "closed":
        await callback.answer(
            "Обращение закрыто. Создайте новое.", show_alert=True
        )
        return
    await callback.answer()
    await state.update_data(reply_ticket_id=str(ticket.id))
    await state.set_state(SupportFlow.reply_body)
    if callback.message is not None:
        await callback.message.answer(
            f"Напишите ответ по обращению {ticket.public_number}."
        )


async def on_pick_ticket(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    text = (message.text or "").strip().upper()
    if text in {"ОТМЕНА", "/CANCEL"}:
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu())
        return
    row = await session.execute(
        select(SupportTicket).where(
            SupportTicket.user_id == db_user.id,
            SupportTicket.public_number == text,
        )
    )
    ticket = row.scalar_one_or_none()
    if ticket is None:
        await message.answer(
            "Обращение не найдено. Номер выглядит так: SUP-000001."
        )
        return
    if ticket.status == "closed":
        await message.answer(
            "Обращение закрыто. Создайте новое: /support → «Новое обращение»."
        )
        return
    await state.update_data(reply_ticket_id=str(ticket.id))
    await state.set_state(SupportFlow.reply_body)
    await message.answer(f"Напишите ответ по обращению {ticket.public_number}.")


async def _save_user_reply(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    ticket = await _owned_ticket(session, db_user.id, str(data.get("reply_ticket_id") or ""))
    if ticket is None:
        await state.clear()
        await message.answer("Обращение недоступно.", reply_markup=main_menu())
        return
    try:
        await add_user_message(
            session,
            ticket=ticket,
            user_id=db_user.id,
            body=message.text or message.caption or "",
            attachment=Attachment.from_telegram(message),
        )
    except SupportError as exc:
        if exc.code == "closed":
            await state.clear()
        await message.answer(exc.message)
        return
    await session.flush()
    await state.clear()
    await message.answer(
        "✅ Сообщение добавлено в обращение. Ответ придёт в этот чат.",
        reply_markup=main_menu(),
    )


def _parse_confirmation(data: str) -> tuple[str, str, int] | None:
    """`sv:yes|<no>:<ticket_id>:<round>` → (action, ticket_id, round)."""
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != "sv" or parts[1] not in {"yes", "no"}:
        return None
    try:
        return parts[1], parts[2], int(parts[3])
    except (ValueError, TypeError):
        return None


async def _drop_buttons(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - old/inaccessible message
        pass


async def on_resolution_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    parsed = _parse_confirmation(callback.data or "")
    if parsed is None:
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    action, raw_id, round_token = parsed
    ticket = await _owned_ticket(session, db_user.id, raw_id)
    if ticket is None:
        await callback.answer("Обращение недоступно.", show_alert=True)
        return
    if int(ticket.confirmation_round or 0) != round_token:
        await _drop_buttons(callback)
        await callback.answer(
            "Это подтверждение уже неактуально. Откройте «🆘 Поддержка».",
            show_alert=True,
        )
        return
    if ticket.status == "resolved":
        await _drop_buttons(callback)
        await callback.answer("Обращение уже завершено.")
        return
    if ticket.status != "awaiting_confirmation":
        await _drop_buttons(callback)
        await callback.answer(
            "Обращение уже не ждёт подтверждения.", show_alert=True
        )
        return
    try:
        if action == "yes":
            changed = confirm_resolution(ticket, user_id=db_user.id, session=session)
            if changed:
                add_system_message(
                    session,
                    ticket=ticket,
                    body="Пользователь подтвердил, что вопрос решён.",
                )
            await session.flush()
            await session.commit()
        else:
            changed = reject_resolution(ticket, user_id=db_user.id, session=session)
            if changed:
                add_system_message(
                    session,
                    ticket=ticket,
                    body="Пользователь сообщил, что вопрос не решён.",
                )
            await session.flush()
            await session.commit()
    except SupportError as exc:
        await session.rollback()
        await callback.answer(exc.message, show_alert=True)
        return

    await _drop_buttons(callback)
    await callback.answer()
    if action == "yes":
        await state.clear()
        if changed and callback.message is not None:
            await callback.message.answer(
                "✅ Обращение завершено. Если понадобится помощь, "
                "вы всегда можете написать нам снова.",
                reply_markup=main_menu(),
            )
        return
    await state.update_data(dispute_ticket_id=str(ticket.id))
    await state.set_state(SupportFlow.dispute)
    if callback.message is not None:
        await callback.message.answer(
            "Расскажите, что осталось нерешённым. Мы продолжим разбираться "
            "в вашем вопросе: можно отправить текст, фото или документ.",
        )


async def on_dispute_message(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    ticket = await _owned_ticket(
        session, db_user.id, str(data.get("dispute_ticket_id") or "")
    )
    if ticket is None:
        await state.clear()
        await message.answer(
            "Обращение недоступно. Откройте «🆘 Поддержка».", reply_markup=main_menu()
        )
        return
    text = message.text or message.caption or ""
    attachment = Attachment.from_telegram(message)
    if not text.strip() and attachment is None:
        await message.answer("Отправьте текст, фото или документ.")
        return
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
    except SupportError as exc:
        await session.rollback()
        await message.answer(exc.message)
        return
    await state.clear()
    await message.answer(
        f"✅ Добавили в обращение {ticket.public_number}. Сотрудник увидит сообщение "
        "и ответит в этом чате.",
        reply_markup=main_menu(),
    )


def _pending_attachment(data: dict) -> Attachment | None:
    kind = data.get("pending_attachment_kind")
    file_id = data.get("pending_attachment_file_id")
    if not kind or not file_id:
        return None
    return Attachment(
        kind=str(kind),
        file_id=str(file_id),
        name=data.get("pending_attachment_name"),
        size=data.get("pending_attachment_size"),
    )


async def attach_pending_user_message(
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    ticket: SupportTicket,
) -> bool:
    """Attach a stored unsolicited message to an explicitly chosen ticket."""
    data = await state.get_data()
    text = str(data.get("pending_support_text") or "")
    attachment = _pending_attachment(data)
    if not text.strip() and attachment is None:
        return False
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
    await state.update_data(
        pending_support_text=None,
        pending_attachment_kind=None,
        pending_attachment_file_id=None,
        pending_attachment_name=None,
        pending_attachment_size=None,
    )
    await state.clear()
    return True


async def on_choose_ticket(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    ticket = await _owned_ticket(session, db_user.id, (callback.data or "")[len(CB_PICK) :])
    if ticket is None:
        await callback.answer("Обращение недоступно.", show_alert=True)
        return
    ok = await attach_pending_user_message(session, db_user, state, ticket)
    if not ok:
        await callback.answer("Сообщение уже отправлено или устарело.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            f"✅ Сообщение добавлено в обращение {ticket.public_number}. "
            "Сотрудник увидит его и ответит в этом чате.",
            reply_markup=main_menu(),
        )


def create_router() -> Router:
    router = Router(name="support")
    router.message.register(open_support, Command("support"))
    router.message.register(open_support, F.text == MENU_SUPPORT)
    router.message.register(on_category, SupportFlow.category, F.text)
    router.message.register(on_subject, SupportFlow.subject, F.text)
    router.message.register(on_body, SupportFlow.body)
    router.message.register(on_pick_ticket, SupportFlow.pick_ticket, F.text)
    router.message.register(on_body, SupportFlow.reply_body)
    router.message.register(on_dispute_message, SupportFlow.dispute)
    router.callback_query.register(
        on_resolution_callback, F.data.startswith(CB_YES)
    )
    router.callback_query.register(
        on_resolution_callback, F.data.startswith(CB_NO)
    )
    router.callback_query.register(on_choose_ticket, F.data.startswith(CB_PICK))
    router.callback_query.register(on_category_cb, F.data.startswith(CB_CAT))
    router.callback_query.register(on_category_cb, F.data == CB_NEW)
    router.callback_query.register(on_list_tickets, F.data == CB_LIST)
    router.callback_query.register(on_open_ticket, F.data.startswith(CB_OPEN))
    router.callback_query.register(on_reply_ticket, F.data.startswith(CB_REPLY))
    return router
