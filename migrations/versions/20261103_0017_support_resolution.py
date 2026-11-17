"""Support tickets: explicit completion author and time.

Final staff reply now moves a ticket to `resolved`, so the queue and the ticket
card need to show who finished it and when. Both columns are nullable: old
tickets have no completion fact and must not be backfilled by guessing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261103_0017"
down_revision = "20261102_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "support_tickets",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column("resolved_by_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_support_tickets_resolved_by",
        "support_tickets",
        "admin_accounts",
        ["resolved_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_support_tickets_resolved_by_id",
        "support_tickets",
        ["resolved_by_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_resolved_by_id", table_name="support_tickets")
    op.drop_constraint(
        "fk_support_tickets_resolved_by", "support_tickets", type_="foreignkey"
    )
    op.drop_column("support_tickets", "resolved_by_id")
    op.drop_column("support_tickets", "resolved_at")
