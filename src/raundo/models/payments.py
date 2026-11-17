"""Payment orders, provider transactions and Premium entitlement linkage.

Money is always stored in integer minor units (kopecks for RUB, whole Stars for
XTR). The order snapshots plan, period and price so a catalog change can never
alter an existing checkout.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from raundo.db.base import Base, TimestampMixin

ORDER_CREATED = "created"
ORDER_PENDING = "pending"
ORDER_PAID = "paid"
ORDER_CANCELED = "canceled"
ORDER_EXPIRED = "expired"
ORDER_REFUNDED = "refunded"

PROVIDER_DEMO = "demo"
PROVIDER_YOOKASSA = "yookassa"
PROVIDER_STARS = "stars"


class PaymentOrder(Base, TimestampMixin):
    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint("order_number", name="uq_payment_order_number"),
        UniqueConstraint("checkout_token_hash", name="uq_payment_order_token"),
        Index("ix_payment_orders_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(24), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_code: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    # yookassa | stars | demo
    payment_provider: Mapped[str] = mapped_column(String(32), nullable=False, default=PROVIDER_DEMO)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ORDER_CREATED, index=True
    )
    # The raw token is only in the checkout link; the DB stores its SHA-256.
    checkout_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    checkout_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Editions of the legal documents that applied when the order was created.
    offer_edition: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    terms_edition: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    consent_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="SET NULL"), nullable=True
    )
    target_organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="SET NULL"), nullable=True
    )
    premium_subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("premium_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Entitlement snapshot: allows a refund to undo exactly this purchase.
    subscription_extended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subscription_expires_before: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_expires_after: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_payment_id", name="uq_payment_tx_provider_payment"
        ),
        Index("ix_payment_tx_order", "order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("payment_orders.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_charge_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(32), nullable=False)
    refund_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Minimal provider diagnostics: never card data or secrets.
    safe_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
