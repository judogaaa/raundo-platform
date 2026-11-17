"""Self-employed (НПД) receipt ledger.

YooKassa no longer auto-registers НПД receipts, so RAUNDO keeps an explicit,
auditable ledger: every confirmed payment gets a row that must be marked as
registered in «Мой налог» (or a verified partner integration) with the real
ФНС receipt id/URL. Nothing here fabricates a receipt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.payments import PaymentOrder
from raundo.models.tax import TaxReceipt

RECEIPT_PENDING = "pending"
RECEIPT_REGISTERED = "registered"
RECEIPT_FAILED = "failed"
RECEIPT_REFUNDED = "refunded"

VALID_STATUSES = (RECEIPT_PENDING, RECEIPT_REGISTERED, RECEIPT_FAILED, RECEIPT_REFUNDED)


class ReceiptError(Exception):
    pass


async def ensure_pending_receipt(session: AsyncSession, order: PaymentOrder) -> TaxReceipt:
    """Create the ledger row for a paid order exactly once."""
    existing = await session.scalar(
        select(TaxReceipt).where(TaxReceipt.order_id == order.id)
    )
    if existing is not None:
        return existing
    receipt = TaxReceipt(
        order_id=order.id,
        order_number=order.order_number,
        amount_minor=order.amount_minor,
        currency=order.currency,
        buyer_reference=str(order.user_id),
        tax_status="self_employed",
        status=RECEIPT_PENDING,
        paid_at=datetime.now(timezone.utc),
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def mark_registered(
    session: AsyncSession,
    receipt_id: uuid.UUID,
    *,
    provider: str,
    fiscal_receipt_id: str,
    receipt_url: str | None = None,
    sent_to_user: bool = False,
) -> TaxReceipt:
    receipt = await session.get(TaxReceipt, receipt_id)
    if receipt is None:
        raise ReceiptError("receipt not found")
    if not fiscal_receipt_id.strip():
        raise ReceiptError("fiscal receipt id is required")
    receipt.status = RECEIPT_REGISTERED
    receipt.provider = provider.strip()[:64]
    receipt.fiscal_receipt_id = fiscal_receipt_id.strip()[:128]
    receipt.receipt_url = (receipt_url or "").strip()[:512] or None
    receipt.registered_at = datetime.now(timezone.utc)
    receipt.sent_to_user = sent_to_user
    receipt.error = None
    await session.flush()
    # Send the real receipt link to the buyer (only when the link exists).
    if receipt.receipt_url:
        from raundo.services.notifications import NotificationService

        order = await session.get(PaymentOrder, receipt.order_id)
        if order is not None:
            await NotificationService(session).enqueue(
                kind="receipt.issued",
                dedupe_key=f"receipt_issued:{receipt.id}",
                recipient_user_id=order.user_id,
                resource_type="tax_receipt",
                resource_id=str(receipt.id),
                safe_payload={
                    "order_number": receipt.order_number,
                    "receipt_url": receipt.receipt_url,
                },
            )
    return receipt


async def mark_failed(session: AsyncSession, receipt_id: uuid.UUID, *, error: str) -> TaxReceipt:
    receipt = await session.get(TaxReceipt, receipt_id)
    if receipt is None:
        raise ReceiptError("receipt not found")
    receipt.status = RECEIPT_FAILED
    receipt.error = (error or "").strip()[:500] or "unknown error"
    await session.flush()
    return receipt


async def mark_refunded(
    session: AsyncSession, order_id: uuid.UUID, *, error: str | None = None
) -> TaxReceipt | None:
    receipt = await session.scalar(select(TaxReceipt).where(TaxReceipt.order_id == order_id))
    if receipt is None:
        return None
    receipt.status = RECEIPT_REFUNDED
    receipt.error = (error or "").strip()[:500] or None
    await session.flush()
    return receipt


async def missing_receipts(session: AsyncSession, limit: int = 100) -> list[TaxReceipt]:
    """Paid orders without a registered receipt, oldest first."""
    rows = (
        await session.scalars(
            select(TaxReceipt)
            .where(TaxReceipt.status.in_((RECEIPT_PENDING, RECEIPT_FAILED)))
            .order_by(TaxReceipt.created_at)
            .limit(limit)
        )
    ).all()
    return list(rows)
