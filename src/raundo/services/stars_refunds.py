"""Refund a paid Telegram Stars order through Bot API refundStarPayment."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.payments import PROVIDER_STARS, PaymentOrder, PaymentTransaction
from raundo.models.user import User
from raundo.services import payments as payment_service


class StarsRefundError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


async def refund_stars_order(
    session: AsyncSession,
    bot: Bot,
    order: PaymentOrder,
    *,
    actor_id: int | None,
) -> PaymentOrder:
    if order.payment_provider != PROVIDER_STARS:
        raise StarsRefundError("not_stars")
    if order.status != payment_service.ORDER_PAID:
        raise StarsRefundError("not_paid")
    tx = await session.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.order_id == order.id,
            PaymentTransaction.provider == PROVIDER_STARS,
        )
    )
    charge_id = tx.provider_charge_id if tx is not None else None
    if not charge_id:
        raise StarsRefundError("no_charge")
    user = await session.get(User, order.user_id)
    if user is None:
        raise StarsRefundError("no_user")
    try:
        await bot.refund_star_payment(
            user_id=user.telegram_id,
            telegram_payment_charge_id=charge_id,
        )
    except TelegramBadRequest as exc:
        raise StarsRefundError(f"telegram:{exc.message}") from exc
    refunded = await payment_service.refund_order(
        session,
        order=order,
        refund_status="refunded",
        reason=f"stars_refund_by:{actor_id}",
        transaction=tx,
    )
    return refunded
