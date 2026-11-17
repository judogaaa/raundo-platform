"""Startup feed reactions (one active vote per user/startup/14-day period)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261102_0014"
down_revision = "20261031_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "startup_reactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("period_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "startup_id", "user_id", "period_index", name="uq_startup_reaction_period"
        ),
    )
    op.create_index(
        "ix_startup_reactions_startup_id", "startup_reactions", ["startup_id"]
    )
    op.create_index("ix_startup_reactions_user_id", "startup_reactions", ["user_id"])
    op.create_index(
        "ix_startup_reactions_period_index", "startup_reactions", ["period_index"]
    )
    op.create_index(
        "ix_startup_reactions_period_kind",
        "startup_reactions",
        ["period_index", "kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_startup_reactions_period_kind", table_name="startup_reactions")
    op.drop_index("ix_startup_reactions_period_index", table_name="startup_reactions")
    op.drop_index("ix_startup_reactions_user_id", table_name="startup_reactions")
    op.drop_index("ix_startup_reactions_startup_id", table_name="startup_reactions")
    op.drop_table("startup_reactions")
