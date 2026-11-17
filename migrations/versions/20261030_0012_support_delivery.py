"""Support attachments, reply delivery links and complaint resolutions.

Revision ID: 20261030_0012
Revises: 20261030_0011
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261030_0012"
down_revision: Union[str, None] = "20261030_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)}


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    ticket_columns = _column_names("support_tickets")
    if "category" not in ticket_columns:
        op.add_column("support_tickets", sa.Column("category", sa.String(length=32), nullable=True))

    message_columns = _column_names("support_messages")
    if "attachment_kind" not in message_columns:
        op.add_column(
            "support_messages", sa.Column("attachment_kind", sa.String(length=16), nullable=True)
        )
    if "attachment_name" not in message_columns:
        op.add_column(
            "support_messages", sa.Column("attachment_name", sa.String(length=255), nullable=True)
        )
    if "attachment_size" not in message_columns:
        op.add_column("support_messages", sa.Column("attachment_size", sa.Integer(), nullable=True))
    if "attachment_file_id" not in message_columns:
        op.add_column(
            "support_messages", sa.Column("attachment_file_id", sa.String(length=255), nullable=True)
        )
    if "outbox_event_id" not in message_columns:
        op.add_column("support_messages", sa.Column("outbox_event_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_support_messages_outbox_event_id",
            "support_messages",
            "outbox_events",
            ["outbox_event_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "ix_support_messages_outbox_event_id" not in _index_names("support_messages"):
        op.create_index(
            "ix_support_messages_outbox_event_id",
            "support_messages",
            ["outbox_event_id"],
            unique=False,
        )

    complaint_columns = _column_names("complaints")
    if "resolution_comment" not in complaint_columns:
        op.add_column("complaints", sa.Column("resolution_comment", sa.Text(), nullable=True))
    if "resolved_by" not in complaint_columns:
        op.add_column("complaints", sa.Column("resolved_by", sa.Uuid(), nullable=True))
    if "resolved_at" not in complaint_columns:
        op.add_column(
            "complaints",
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("complaints", "resolved_at")
    op.drop_column("complaints", "resolved_by")
    op.drop_column("complaints", "resolution_comment")
    op.drop_index("ix_support_messages_outbox_event_id", table_name="support_messages")
    op.drop_constraint("fk_support_messages_outbox_event_id", "support_messages", type_="foreignkey")
    op.drop_column("support_messages", "outbox_event_id")
    op.drop_column("support_messages", "attachment_file_id")
    op.drop_column("support_messages", "attachment_size")
    op.drop_column("support_messages", "attachment_name")
    op.drop_column("support_messages", "attachment_kind")
    op.drop_column("support_tickets", "category")
