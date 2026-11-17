"""Complaint priority and attachment metadata.

Revision ID: 20261031_0013
Revises: 20261030_0012
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261031_0013"
down_revision: Union[str, None] = "20261030_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def _index_names(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    columns = _column_names("complaints")
    if "priority" not in columns:
        op.add_column(
            "complaints",
            sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        )
        op.alter_column("complaints", "priority", server_default=None)
    if "attachment_kind" not in columns:
        op.add_column("complaints", sa.Column("attachment_kind", sa.String(length=16), nullable=True))
    if "attachment_name" not in columns:
        op.add_column("complaints", sa.Column("attachment_name", sa.String(length=255), nullable=True))
    if "attachment_size" not in columns:
        op.add_column("complaints", sa.Column("attachment_size", sa.Integer(), nullable=True))
    if "attachment_file_id" not in columns:
        op.add_column(
            "complaints", sa.Column("attachment_file_id", sa.String(length=255), nullable=True)
        )
    if "ix_complaints_priority" not in _index_names("complaints"):
        op.create_index("ix_complaints_priority", "complaints", ["priority"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_complaints_priority", table_name="complaints")
    op.drop_column("complaints", "attachment_file_id")
    op.drop_column("complaints", "attachment_size")
    op.drop_column("complaints", "attachment_name")
    op.drop_column("complaints", "attachment_kind")
    op.drop_column("complaints", "priority")
