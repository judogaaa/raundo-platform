"""Order-level snapshots needed to roll back an entitlement on refund.

Revision ID: 20261104_0022
Revises: 20261104_0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261104_0022"
down_revision = "20261104_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_orders",
        sa.Column("subscription_extended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "payment_orders",
        sa.Column("subscription_expires_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payment_orders",
        sa.Column("subscription_expires_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_orders", "subscription_expires_after")
    op.drop_column("payment_orders", "subscription_expires_before")
    op.drop_column("payment_orders", "subscription_extended")
