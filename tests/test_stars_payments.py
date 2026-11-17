"""Telegram Stars: pre-checkout validation and idempotent activation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from raundo.config import Settings
from raundo.models.billing import PremiumSubscription
from raundo.models.payments import PaymentOrder, PaymentTransaction
from raundo.models.system import OutboxEvent
from raundo.routers.premium import on_pre_checkout, on_successful_payment
from raundo.services import payments as pay
from raundo.services.accounts import AccountService

STARS_SETTINGS = Settings(
    _env_file=None,
    PAYMENTS_MODE="provider_test",
    PAYMENTS_STARS_ENABLED="true",
    PAYMENTS_CARD_ENABLED="true",
)


class FakeState:
    def __init__(self) -> None:
        self.clear = AsyncMock()


def _query(order: PaymentOrder, *, user_id: int, amount: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        invoice_payload=str(order.id),
        currency="XTR",
        total_amount=order.amount_minor if amount is None else amount,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


def _message(order: PaymentOrder, *, amount: int | None = None, charge: str = "charge_1") -> SimpleNamespace:
    return SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload=str(order.id),
            currency="XTR",
            total_amount=order.amount_minor if amount is None else amount,
            telegram_payment_charge_id=charge,
        ),
        answer=AsyncMock(),
    )


async def _order(session, telegram_id: int = 79501):  # noqa: ANN001, ANN202
    user = await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"stars{telegram_id}",
        first_name="Stars",
        last_name="User",
        language_code="ru",
    )
    order, _token = await pay.create_order(
        session,
        user=user,
        plan_code="investor_pro",
        duration_code="7d",
        provider=pay.PROVIDER_STARS,
        settings=STARS_SETTINGS,
    )
    await session.flush()
    return user, order


@pytest.mark.asyncio
async def test_stars_order_uses_xtr_and_validates_pre_checkout(session) -> None:
    user, order = await _order(session)
    assert order.currency == "XTR"
    assert order.amount_minor == 1000
    query = _query(order, user_id=user.telegram_id)
    await on_pre_checkout(query, session, user)
    assert query.answer.await_args.kwargs["ok"] is True

    wrong_amount = _query(order, user_id=user.telegram_id, amount=1)
    await on_pre_checkout(wrong_amount, session, user)
    assert wrong_amount.answer.await_args.kwargs["ok"] is False

    stranger = await AccountService(session).bind_telegram_user(
        telegram_id=79599,
        username="stranger_stars",
        first_name="S",
        last_name=None,
        language_code="ru",
    )
    foreign = _query(order, user_id=stranger.telegram_id)
    await on_pre_checkout(foreign, session, stranger)
    assert foreign.answer.await_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_successful_payment_activates_once(session) -> None:
    user, order = await _order(session, 79502)
    state = FakeState()
    message = _message(order)
    await on_successful_payment(message, session, user, state)  # type: ignore[arg-type]
    await session.commit()

    subs = (
        await session.execute(
            select(PremiumSubscription).where(PremiumSubscription.user_id == user.id)
        )
    ).scalars().all()
    assert len(subs) == 1
    assert subs[0].plan_code == "investor_pro"
    tx = await session.scalar(
        select(PaymentTransaction).where(PaymentTransaction.provider_payment_id == "charge_1")
    )
    assert tx is not None and tx.provider == "stars"
    events = (
        await session.execute(
            select(OutboxEvent).where(OutboxEvent.resource_id == str(order.id))
        )
    ).scalars().all()
    assert len(events) == 1

    # A duplicate successful_payment must not grant twice.
    repeat = _message(order, charge="charge_1")
    await on_successful_payment(repeat, session, user, state)  # type: ignore[arg-type]
    await session.commit()
    subs = (
        await session.execute(
            select(PremiumSubscription).where(PremiumSubscription.user_id == user.id)
        )
    ).scalars().all()
    assert len(subs) == 1
    assert "уже активирована" in repeat.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_stars_wrong_amount_does_not_activate(session) -> None:
    user, order = await _order(session, 79503)
    message = _message(order, amount=1)
    await on_successful_payment(message, session, user, FakeState())  # type: ignore[arg-type]
    await session.commit()
    subs = (
        await session.execute(
            select(PremiumSubscription).where(PremiumSubscription.user_id == user.id)
        )
    ).scalars().all()
    assert subs == []
    assert message.answer.await_count == 0
    stored = await session.get(PaymentOrder, order.id)
    assert stored is not None and stored.status != "paid"
