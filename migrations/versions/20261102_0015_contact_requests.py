"""Contact requests between a viewer and a startup founder."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261102_0015"
down_revision = "20261102_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("startup_id", "requester_user_id", name="uq_contact_request_pair"),
    )
    op.create_index("ix_contact_requests_startup_id", "contact_requests", ["startup_id"])
    op.create_index(
        "ix_contact_requests_requester_user_id", "contact_requests", ["requester_user_id"]
    )
    op.create_index("ix_contact_requests_owner_user_id", "contact_requests", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_requests_owner_user_id", table_name="contact_requests")
    op.drop_index("ix_contact_requests_requester_user_id", table_name="contact_requests")
    op.drop_index("ix_contact_requests_startup_id", table_name="contact_requests")
    op.drop_table("contact_requests")
