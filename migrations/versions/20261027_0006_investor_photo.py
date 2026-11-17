"""Investor profile photo / organization logo file_id.

Revision ID: 20261027_0006
Revises: 20261026_0005
Create Date: 2026-10-27

Nullable Telegram file identifiers. Existing profiles stay valid without a photo.
Does not approve organization affiliation. Does not touch startup media.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0006"
down_revision: Union[str, None] = "20261026_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "investor_profiles",
        sa.Column("telegram_photo_file_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "investor_profiles",
        sa.Column("telegram_photo_unique_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("investor_profiles", "telegram_photo_unique_id")
    op.drop_column("investor_profiles", "telegram_photo_file_id")
