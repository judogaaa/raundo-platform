"""Owner edit/delete lifecycle: soft delete and published snapshots.

Revision ID: 20261027_0007
Revises: 20261027_0006
Create Date: 2026-10-27

Nullable deleted_at and published_snapshot JSON for re-moderation without
exposing unapproved edits. Does not touch live raundo until deployed separately.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261027_0007"
down_revision: Union[str, None] = "20261027_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "startups",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "startups",
        sa.Column("published_snapshot", JSONB, nullable=True),
    )
    op.add_column(
        "investor_profiles",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "investor_profiles",
        sa.Column("published_snapshot", JSONB, nullable=True),
    )
    op.create_index("ix_startups_deleted_at", "startups", ["deleted_at"])
    op.create_index("ix_investor_profiles_deleted_at", "investor_profiles", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_investor_profiles_deleted_at", table_name="investor_profiles")
    op.drop_index("ix_startups_deleted_at", table_name="startups")
    op.drop_column("investor_profiles", "published_snapshot")
    op.drop_column("investor_profiles", "deleted_at")
    op.drop_column("startups", "published_snapshot")
    op.drop_column("startups", "deleted_at")
