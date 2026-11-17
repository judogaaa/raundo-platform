"""Consent detail, age brackets, account deletion markers, order editions.

Additive migration: existing rows keep working. Old `terms`/`privacy` consents
remain valid records for the edition they were granted under; new required
`data_processing` consent is collected on the next use of a gated feature.

Revision ID: 20261104_0020
Revises: 20261103_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261104_0020"
down_revision = "20261103_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_consents", sa.Column("purpose", sa.String(length=32), nullable=True))
    op.add_column("user_consents", sa.Column("audience", sa.String(length=32), nullable=True))
    op.add_column("user_consents", sa.Column("fields", sa.JSON(), nullable=True))
    op.add_column("user_consents", sa.Column("source", sa.String(length=32), nullable=True))
    op.add_column(
        "user_consents", sa.Column("revoke_reason", sa.String(length=64), nullable=True)
    )

    op.add_column("users", sa.Column("age_bracket", sa.String(length=16), nullable=True))
    op.add_column(
        "users", sa.Column("age_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("payment_orders", sa.Column("offer_edition", sa.String(length=16), nullable=True))
    op.add_column("payment_orders", sa.Column("terms_edition", sa.String(length=16), nullable=True))

    op.create_index(
        "ix_user_consents_user_kind",
        "user_consents",
        ["user_id", "kind"],
        unique=False,
    )
    op.create_index(
        "ix_users_age_bracket",
        "users",
        ["age_bracket"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_users_age_bracket", table_name="users")
    op.drop_index("ix_user_consents_user_kind", table_name="user_consents")
    op.drop_column("payment_orders", "terms_edition")
    op.drop_column("payment_orders", "offer_edition")
    op.drop_column("users", "anonymized_at")
    op.drop_column("users", "deletion_requested_at")
    op.drop_column("users", "age_confirmed_at")
    op.drop_column("users", "age_bracket")
    op.drop_column("user_consents", "revoke_reason")
    op.drop_column("user_consents", "source")
    op.drop_column("user_consents", "fields")
    op.drop_column("user_consents", "audience")
    op.drop_column("user_consents", "purpose")
