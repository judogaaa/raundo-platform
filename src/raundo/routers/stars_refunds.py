"""Admin refund for Telegram Stars orders (refundStarPayment)."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import get_settings
from raundo.models.payments import PaymentOrder
from raundo.services.stars_refunds import StarsRefundError, refund_stars_order


async def refund_stars_command(message: Message, session: AsyncSession) -> None:
    settings = get_settings()
    actor_id = message.from_user.id if message.from_user else None
    if not settings.is_admin(actor_id):
        await message.answer("Команда доступна только администраторам.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Использование: /refund_stars PAY-000001")
        return
    order = await session.scalar(
        select(PaymentOrder).where(PaymentOrder.order_number == parts[1])
    )
    if order is None:
        await message.answer("Заказ с таким номером не найден.")
        return
    try:
        await refund_stars_order(session, message.bot, order, actor_id=actor_id)
        await session.commit()
    except StarsRefundError as exc:
        await session.rollback()
        messages = {
            "not_stars": "Это не Stars-заказ.",
            "not_paid": "Возврат возможен только для оплаченного Stars-заказа.",
            "no_charge": "У заказа нет telegram_payment_charge_id, возврат невозможен.",
            "no_user": "Пользователь заказа не найден.",
        }
        await message.answer(messages.get(exc.code, f"Возврат отклонён: {exc.code}"))
        return
    await message.answer(
        f"Возврат Stars по заказу {order.order_number} выполнен. "
        "Оплаченный период скорректирован по условиям возврата."
    )


def create_router() -> Router:
    router = Router(name="stars_refunds")
    router.message.register(refund_stars_command, Command("refund_stars"))
    return router
