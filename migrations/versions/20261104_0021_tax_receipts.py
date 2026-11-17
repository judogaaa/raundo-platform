"""Ledger of self-employed tax receipts for paid orders.

Revision ID: 20261104_0021
Revises: 20261104_0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261104_0021"
down_revision = "20261104_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tax_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Uuid(),
            sa.ForeignKey("payment_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_number", sa.String(length=24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("buyer_reference", sa.String(length=64), nullable=True),
        sa.Column("tax_status", sa.String(length=32), nullable=False, server_default="self_employed"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("fiscal_receipt_id", sa.String(length=128), nullable=True),
        sa.Column("receipt_url", sa.String(length=512), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_to_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tax_receipts_status", "tax_receipts", ["status"], unique=False)
    op.create_index("ix_tax_receipts_order", "tax_receipts", ["order_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tax_receipts_order", table_name="tax_receipts")
    op.drop_index("ix_tax_receipts_status", table_name="tax_receipts")
    op.drop_table("tax_receipts")
