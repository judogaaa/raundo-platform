"""Support: the user confirms the resolution in Telegram.

A staff answer now moves a ticket to `awaiting_confirmation`; only the user's
"yes" completes it. The migration is additive and leaves existing tickets
readable: no status is rewritten and old rows get round 0 / NULL facts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261103_0018"
down_revision = "20261103_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "support_tickets",
        sa.Column(
            "confirmation_round",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "support_tickets",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "support_tickets",
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_support_tickets_confirmed_by",
        "support_tickets",
        "users",
        ["confirmed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_support_tickets_confirmed_by", "support_tickets", type_="foreignkey"
    )
    op.drop_column("support_tickets", "disputed_at")
    op.drop_column("support_tickets", "confirmed_by_user_id")
    op.drop_column("support_tickets", "confirmed_at")
    op.drop_column("support_tickets", "confirmation_round")
