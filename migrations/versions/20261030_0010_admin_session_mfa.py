"""MFA gate on admin sessions and complaint reporter index.

Revision ID: 20261030_0010
Revises: 20261029_0009
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_0010"
down_revision: Union[str, None] = "20261029_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_sessions",
        sa.Column("mfa_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_complaints_reporter_user_id", "complaints", ["reporter_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_complaints_reporter_user_id", table_name="complaints")
    op.drop_column("admin_sessions", "mfa_verified_at")
