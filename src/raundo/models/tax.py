"""Tax receipt ledger for НПД payments (no fiscal data is fabricated)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from raundo.db.base import Base, TimestampMixin


class TaxReceipt(Base, TimestampMixin):
    __tablename__ = "tax_receipts"
    __table_args__ = (
        Index("ix_tax_receipts_status", "status"),
        Index("ix_tax_receipts_order", "order_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="CASCADE"), nullable=False
    )
    order_number: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    # Internal reference only (no extra personal data copied from the buyer).
    buyer_reference: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tax_status: Mapped[str] = mapped_column(String(32), nullable=False, default="self_employed")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fiscal_receipt_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    receipt_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_to_user: Mapped[bool] = mapped_column(default=False, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
