"""Complaint intake inside @RaundoAppBot.

A viewer can report a published startup, an approved investor, an organization or
describe a different problem. Everything lands in PostgreSQL and is reviewed in the web
admin; the reporter gets a real Telegram notification when a decision is made.
"""

from __future__ import annotations

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
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo import ui
from raundo.domain import REVIEW_APPROVED
from raundo.keyboards import main_menu
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization
from raundo.models.startup import Startup
from raundo.models.user import User
from raundo.services.complaints import (
    MAX_ATTACHMENT_BYTES,
    ComplaintError,
    Evidence,
    create_complaint,
)

# User-selectable targets. "user" is intentionally absent: until direct messaging
# exists, a viewer cannot name another person's internal id safely.
TYPES = {
    "startup": "Стартап",
    "investor": "Инвестор",
    "organization": "Организация",
    "other": "Другое",
}
SKIP_WORDS = {"пропустить", "нет", "без вложения", "-"}

CB_TYPE = "cmp:type:"
CB_PICK = "cmp:pick:"
CB_CANCEL = "cmp:cancel"


class ComplaintFlow(StatesGroup):
    target_type = State()
    target_search = State()
    target_pick = State()
    reason = State()
    evidence = State()


def _types_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🚀 {TYPES['startup']}", callback_data=f"{CB_TYPE}startup"
            ),
            InlineKeyboardButton(
                text=f"💼 {TYPES['investor']}", callback_data=f"{CB_TYPE}investor"
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"🏢 {TYPES['organization']}", callback_data=f"{CB_TYPE}organization"
            ),
            InlineKeyboardButton(
                text=f"✍️ {TYPES['other']}", callback_data=f"{CB_TYPE}other"
            ),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_complaint(message: Message, state: FSMContext) -> None:
    await state.set_state(ComplaintFlow.target_type)
    await message.answer(
        "⚠️ <b>Жалоба в RAUNDO</b>\n\n"
        "Сообщите о проблеме: мы проверим её и ответим в этом чате.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("На что жалуетесь?", reply_markup=_types_keyboard())


def _cancelled(message: Message) -> bool:
    return (message.text or "").strip().lower() in {"отмена", "/cancel"}


def _pick_type(text: str) -> str | None:
    clean = (text or "").strip().lower()
    if clean.isdigit():
        keys = list(TYPES)
        index = int(clean) - 1
        return keys[index] if 0 <= index < len(keys) else None
    for code, label in TYPES.items():
        if code in clean or label.lower() in clean:
            return code
    return None


async def on_type(
    message: Message, state: FSMContext, db_user: User
) -> None:
    if _cancelled(message):
        await state.clear()
        await message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    key = _pick_type(message.text or "")
    if key is None:
        await message.answer("Выберите вариант кнопкой выше.")
        return
    await _after_type(message, state, key)


async def _after_type(message: Message, state: FSMContext, key: str) -> None:
    await state.update_data(target_type=key, candidates=[])
    if key == "other":
        await state.set_state(ComplaintFlow.reason)
        await message.answer(
            "Опишите проблему одним сообщением, не короче 10 символов."
        )
        return
    await state.set_state(ComplaintFlow.target_search)
    await message.answer(
        f"Напишите название: {TYPES[key].lower()}. Покажу подходящие варианты."
    )


async def on_type_cb(callback: CallbackQuery, state: FSMContext) -> None:
    data = callback.data or ""
    if data == CB_CANCEL:
        await callback.answer("Отменено")
        await state.clear()
        if callback.message is not None:
            await callback.message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    key = data[len(CB_TYPE) :] if data.startswith(CB_TYPE) else ""
    if key not in TYPES:
        await callback.answer("Кнопка устарела. Откройте /complaint.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _after_type(callback.message, state, key)


async def _search(
    session: AsyncSession, target_type: str, needle: str
) -> list[tuple[str, str]]:
    like = f"%{needle}%"
    if target_type == "startup":
        rows = await session.execute(
            select(Startup.id, Startup.name)
            .where(
                Startup.deleted_at.is_(None),
                Startup.review_state == REVIEW_APPROVED,
                Startup.name.ilike(like),
            )
            .order_by(Startup.name)
            .limit(5)
        )
    elif target_type == "investor":
        rows = await session.execute(
            select(InvestorProfile.id, InvestorProfile.display_name)
            .where(
                InvestorProfile.deleted_at.is_(None),
                InvestorProfile.review_status == REVIEW_APPROVED,
                or_(
                    InvestorProfile.display_name.ilike(like),
                    InvestorProfile.public_reference_url.ilike(like),
                ),
            )
            .order_by(InvestorProfile.display_name)
            .limit(5)
        )
    elif target_type == "organization":
        rows = await session.execute(
            select(FundingOrganization.id, FundingOrganization.name)
            .where(
                FundingOrganization.review_status == REVIEW_APPROVED,
                FundingOrganization.name.ilike(like),
            )
            .order_by(FundingOrganization.name)
            .limit(5)
        )
    else:  # pragma: no cover - guarded by the FSM
        return []
    return [
        (str(row_id), title or "Без названия") for row_id, title in rows.all() if row_id
    ]


async def on_search(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    if _cancelled(message):
        await state.clear()
        await message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    needle = (message.text or "").strip()
    if len(needle) < 2:
        await message.answer("Введите хотя бы 2 символа названия.")
        return
    data = await state.get_data()
    candidates = await _search(session, data.get("target_type", ""), needle)
    if not candidates:
        await message.answer(
            "Ничего не найдено среди опубликованных профилей. Уточните название "
            "или выберите «Другое» в /complaint."
        )
        return
    await state.update_data(candidates=[cid for cid, _ in candidates])
    await state.set_state(ComplaintFlow.target_pick)
    rows = [
        [InlineKeyboardButton(text=title[:60], callback_data=f"{CB_PICK}{index}")]
        for index, (_, title) in enumerate(candidates)
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL)])
    await message.answer(
        "Выберите объект жалобы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def on_pick(
    message: Message, state: FSMContext
) -> None:
    if _cancelled(message):
        await state.clear()
        await message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    text = (message.text or "").strip()
    data = await state.get_data()
    candidates: list[str] = data.get("candidates") or []
    if not text.isdigit() or not (1 <= int(text) <= len(candidates)):
        await message.answer("Выберите объект кнопкой выше.")
        return
    await _after_pick(message, state, int(text) - 1)


async def _after_pick(message: Message, state: FSMContext, index: int) -> None:
    data = await state.get_data()
    candidates: list[str] = data.get("candidates") or []
    if not (0 <= index < len(candidates)):
        await message.answer("Вариант устарел. Начните заново: /complaint.")
        return
    await state.update_data(target_id=candidates[index], candidates=[])
    await state.set_state(ComplaintFlow.reason)
    await message.answer("Опишите проблему одним сообщением, не короче 10 символов.")


async def on_pick_cb(callback: CallbackQuery, state: FSMContext) -> None:
    data = callback.data or ""
    try:
        index = int(data[len(CB_PICK) :])
    except (ValueError, TypeError):
        await callback.answer("Кнопка устарела.", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await _after_pick(callback.message, state, index)


async def on_reason(
    message: Message, state: FSMContext
) -> None:
    if _cancelled(message):
        await state.clear()
        await message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("Слишком коротко: нужно минимум 10 символов.")
        return
    await state.update_data(reason=text[:4000])
    await state.set_state(ComplaintFlow.evidence)
    await message.answer(
        "Приложите фото или документ до 5 МБ. Если вложения нет, отправьте «-»."
    )


async def on_evidence(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    if _cancelled(message):
        await state.clear()
        await message.answer("Жалоба отменена.", reply_markup=main_menu())
        return
    data = await state.get_data()
    evidence = None
    if (message.text or "").strip().lower() not in SKIP_WORDS:
        evidence = Evidence.from_telegram(message)
        if evidence is None:
            await message.answer(
                "Пришлите фото или документ либо отправьте «-», чтобы продолжить без вложения."
            )
            return
        if evidence.size is not None and evidence.size > MAX_ATTACHMENT_BYTES:
            await message.answer(
                "Файл больше 5 МБ. Пришлите другой или отправьте «-»."
            )
            return
    try:
        complaint = await create_complaint(
            session,
            reporter_user_id=db_user.id,
            target_type=data.get("target_type", "other"),
            target_id=str(data.get("target_id") or ""),
            reason=data.get("reason") or "",
            evidence=evidence,
        )
    except ComplaintError as exc:
        await message.answer(exc.message)
        if exc.code in {"duplicate", "reason_short", "target_missing"}:
            await state.clear()
        return
    await session.flush()
    await state.clear()
    await message.answer(
        f"✅ Жалоба {ui.b(complaint.public_number)} принята.\n\n"
        "Мы проверим информацию и ответим в этом чате.",
        reply_markup=main_menu(),
    )


def create_router() -> Router:
    router = Router(name="complaints")
    router.message.register(open_complaint, Command("complaint", "report"))
    router.message.register(open_complaint, F.text == "Жалоба")
    router.message.register(on_type, ComplaintFlow.target_type, F.text)
    router.message.register(on_search, ComplaintFlow.target_search, F.text)
    router.message.register(on_pick, ComplaintFlow.target_pick, F.text)
    router.message.register(on_reason, ComplaintFlow.reason, F.text)
    router.message.register(on_evidence, ComplaintFlow.evidence)
    router.callback_query.register(on_type_cb, F.data.startswith(CB_TYPE))
    router.callback_query.register(on_type_cb, F.data == CB_CANCEL)
    router.callback_query.register(on_pick_cb, F.data.startswith(CB_PICK))
    return router
