"""НПД receipt ledger: pending on payment, explicit registration, refunds."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from raundo.models.payments import PaymentOrder
from raundo.models.tax import TaxReceipt
from raundo.services.tax_receipts import (
    RECEIPT_FAILED,
    RECEIPT_PENDING,
    RECEIPT_REFUNDED,
    RECEIPT_REGISTERED,
    ensure_pending_receipt,
    mark_failed,
    mark_refunded,
    mark_registered,
    missing_receipts,
)


def _order(**overrides) -> PaymentOrder:  # noqa: ANN003
    now = datetime.now(timezone.utc)
    data = dict(
        order_number="PAY-700001",
        user_id=uuid.uuid4(),
        plan_code="community_plus",
        duration_code="7d",
        amount_minor=9900,
        currency="RUB",
        payment_provider="demo",
        status="paid",
        checkout_token_hash="hash-" + uuid.uuid4().hex,
        checkout_token_expires_at=now,
        expires_at=now,
    )
    data.update(overrides)
    return PaymentOrder(**data)


@pytest.mark.asyncio
async def test_paid_order_creates_one_pending_receipt(session) -> None:
    order = _order()
    session.add(order)
    await session.flush()

    receipt = await ensure_pending_receipt(session, order)
    again = await ensure_pending_receipt(session, order)
    await session.commit()

    assert receipt.id == again.id
    assert receipt.status == RECEIPT_PENDING
    assert receipt.amount_minor == 9900
    assert receipt.order_number == "PAY-700001"
    assert receipt.tax_status == "self_employed"
    assert receipt.fiscal_receipt_id is None
    rows = (await session.execute(select(TaxReceipt))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_registration_requires_a_real_fiscal_id(session) -> None:
    order = _order(order_number="PAY-700002")
    session.add(order)
    await session.flush()
    receipt = await ensure_pending_receipt(session, order)

    with pytest.raises(Exception):
        await mark_registered(session, receipt.id, provider="moy_nalog", fiscal_receipt_id="  ")

    await mark_registered(
        session,
        receipt.id,
        provider="moy_nalog",
        fiscal_receipt_id="1234567890",
        receipt_url="https://receipt.example/1",
        sent_to_user=True,
    )
    await session.commit()

    assert receipt.status == RECEIPT_REGISTERED
    assert receipt.fiscal_receipt_id == "1234567890"
    assert receipt.sent_to_user is True
    assert receipt.registered_at is not None


@pytest.mark.asyncio
async def test_missing_receipts_lists_pending_and_failed_only(session) -> None:
    paid = _order(order_number="PAY-700003")
    failed = _order(order_number="PAY-700004")
    registered = _order(order_number="PAY-700005")
    session.add_all([paid, failed, registered])
    await session.flush()

    first = await ensure_pending_receipt(session, paid)
    second = await ensure_pending_receipt(session, failed)
    third = await ensure_pending_receipt(session, registered)
    await mark_failed(session, second.id, error="мой налог недоступен")
    await mark_registered(
        session, third.id, provider="moy_nalog", fiscal_receipt_id="999"
    )
    await session.commit()

    missing = await missing_receipts(session)
    ids = {row.id for row in missing}
    assert first.id in ids
    assert second.id in ids
    assert third.id not in ids
    assert second.status == RECEIPT_FAILED


@pytest.mark.asyncio
async def test_refund_marks_receipt(session) -> None:
    order = _order(order_number="PAY-700006")
    session.add(order)
    await session.flush()
    receipt = await ensure_pending_receipt(session, order)
    await mark_refunded(session, order.id, error="refund")
    await session.commit()
    assert receipt.status == RECEIPT_REFUNDED
