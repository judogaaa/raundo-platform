"""Premium payment orders and provider transactions.

Additive only: existing Premium subscriptions and grants are untouched. Amounts
are integer minor units; the raw checkout token is never stored (SHA-256 only).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261103_0019"
down_revision = "20261103_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_number", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_code", sa.String(length=8), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("payment_provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("checkout_token_hash", sa.String(length=128), nullable=False),
        sa.Column("checkout_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(length=64), nullable=True),
        sa.Column("consent_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_startup_id", sa.Uuid(), nullable=True),
        sa.Column("target_organization_id", sa.Uuid(), nullable=True),
        sa.Column("premium_subscription_id", sa.Uuid(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_startup_id"], ["startups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["target_organization_id"], ["funding_organizations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["premium_subscription_id"], ["premium_subscriptions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("order_number", name="uq_payment_order_number"),
        sa.UniqueConstraint("checkout_token_hash", name="uq_payment_order_token"),
    )
    op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"])
    op.create_index("ix_payment_orders_status", "payment_orders", ["status"])
    op.create_index(
        "ix_payment_orders_user_status", "payment_orders", ["user_id", "status"]
    )

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("provider_charge_id", sa.String(length=128), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider_status", sa.String(length=32), nullable=False),
        sa.Column("refund_status", sa.String(length=32), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "provider", "provider_payment_id", name="uq_payment_tx_provider_payment"
        ),
    )
    op.create_index("ix_payment_tx_order", "payment_transactions", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_tx_order", table_name="payment_transactions")
    op.drop_table("payment_transactions")
    op.drop_index("ix_payment_orders_user_status", table_name="payment_orders")
    op.drop_index("ix_payment_orders_status", table_name="payment_orders")
    op.drop_index("ix_payment_orders_user_id", table_name="payment_orders")
    op.drop_table("payment_orders")
