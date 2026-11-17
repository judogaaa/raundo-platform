"""Align admin/support table nullability and indexes with ORM.

Revision ID: 20261030_0011
Revises: 20261030_0010
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_0011"
down_revision: Union[str, None] = "20261030_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES_CREATED_AT = (
    "admin_accounts",
    "admin_audit_log",
    "complaints",
    "support_messages",
    "support_tickets",
    "user_restrictions",
)


def upgrade() -> None:
    for table in _TABLES_CREATED_AT:
        op.alter_column(
            table,
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.alter_column(
        "support_tickets",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "ix_complaints_reporter_user_id" not in {i["name"] for i in insp.get_indexes("complaints")}:
        op.create_index("ix_complaints_reporter_user_id", "complaints", ["reporter_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_complaints_reporter_user_id", table_name="complaints")
    op.alter_column("support_tickets", "updated_at", nullable=True)
    for table in reversed(_TABLES_CREATED_AT):
        op.alter_column(table, "created_at", nullable=True)
