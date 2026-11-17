"""Nullable investor-facing startup profile fields (audience, progress, monetization).

Revision ID: 20261023_0004
Revises: 20261022_0003
Create Date: 2026-10-23

All columns nullable / empty-default so existing drafts (e.g. IMVERA) keep data.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0004"
down_revision: Union[str, None] = "20261022_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "startups",
        sa.Column("audience_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column("startups", sa.Column("audience_note", sa.String(length=240), nullable=True))
    op.add_column(
        "startups",
        sa.Column("progress_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column("startups", sa.Column("monetization_status", sa.String(length=32), nullable=True))
    op.add_column("startups", sa.Column("monetization_note", sa.String(length=240), nullable=True))
    op.add_column("startups", sa.Column("fundraising_stage", sa.String(length=32), nullable=True))
    op.add_column(
        "startups",
        sa.Column(
            "use_of_funds_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
    )
    op.add_column("startups", sa.Column("use_of_funds_note", sa.String(length=240), nullable=True))


def downgrade() -> None:
    op.drop_column("startups", "use_of_funds_note")
    op.drop_column("startups", "use_of_funds_codes")
    op.drop_column("startups", "fundraising_stage")
    op.drop_column("startups", "monetization_note")
    op.drop_column("startups", "monetization_status")
    op.drop_column("startups", "progress_codes")
    op.drop_column("startups", "audience_note")
    op.drop_column("startups", "audience_codes")
