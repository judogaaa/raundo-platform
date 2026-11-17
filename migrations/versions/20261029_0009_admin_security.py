"""Admin account security flags: password rotation and MFA state.

Revision ID: 20261029_0009
Revises: 20261028_0008
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0009"
down_revision: Union[str, None] = "20261028_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_accounts",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "admin_accounts",
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("admin_accounts", sa.Column("backup_codes_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_accounts", "backup_codes_hash")
    op.drop_column("admin_accounts", "mfa_enabled")
    op.drop_column("admin_accounts", "must_change_password")
