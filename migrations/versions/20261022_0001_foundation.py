"""Foundation identity, consents, catalog, outbox and analytics.

Revision ID: 20261022_0001
Revises:
Create Date: 2026-10-22
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SECTORS: tuple[tuple[str, str, str], ...] = (
    ("ai_ml", "AI/ML", "AI/ML"),
    ("saas_b2b", "SaaS/B2B", "SaaS/B2B"),
    ("fintech", "FinTech", "FinTech"),
    ("healthtech", "HealthTech", "HealthTech"),
    ("edtech", "EdTech", "EdTech"),
    ("ecommerce", "E-commerce", "E-commerce"),
    ("marketplace", "Marketplace", "Marketplace"),
    ("industry_robotics", "Industry/Robotics", "Industry/Robotics"),
    ("mobility", "Mobility", "Mobility"),
    ("climate_energy", "Climate/Energy", "Climate/Energy"),
    ("consumer", "Consumer", "Consumer"),
    ("gaming", "Gaming", "Gaming"),
    ("proptech", "PropTech", "PropTech"),
    ("security", "Security", "Security"),
    ("media_creator", "Media/Creator", "Media/Creator"),
    ("other", "Другое", "Other"),
)

STAGES: tuple[tuple[str, str, int], ...] = (
    ("idea", "Idea", 1),
    ("prototype", "Prototype", 2),
    ("mvp", "MVP", 3),
    ("launched", "Launched", 4),
    ("revenue", "Revenue", 5),
    ("scaling", "Scaling", 6),
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Matches SQLAlchemy mapped_column(..., unique=True, index=True) → unique btree index.
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("contact_mode", sa.String(length=32), nullable=False),
        sa.Column("contact_username", sa.String(length=64), nullable=True),
        sa.Column("show_contact_after_accept", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "user_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", "policy_version", name="uq_user_consents_user_kind_version"),
    )
    op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"])

    op.create_table(
        "sectors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name_ru", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "stages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "processed_updates",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("update_id"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("safe_payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_outbox_events_kind", "outbox_events", ["kind"])
    op.create_index("ix_outbox_events_recipient_user_id", "outbox_events", ["recipient_user_id"])
    op.create_index("ix_outbox_events_state", "outbox_events", ["state"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_key_pseudonym", sa.String(length=64), nullable=True),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("properties_safe", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_events_user_key_pseudonym", "analytics_events", ["user_key_pseudonym"])
    op.create_index("ix_analytics_events_event_name", "analytics_events", ["event_name"])

    sectors_table = sa.table(
        "sectors",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name_ru", sa.String()),
        sa.column("name_en", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("taxonomy_version", sa.String()),
    )
    op.bulk_insert(
        sectors_table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name_ru": name_ru,
                "name_en": name_en,
                "active": True,
                "taxonomy_version": "2026-10",
            }
            for code, name_ru, name_en in SECTORS
        ],
    )

    stages_table = sa.table(
        "stages",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        stages_table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "label": label,
                "display_order": order,
                "active": True,
            }
            for code, label, order in STAGES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_events_event_name", table_name="analytics_events")
    op.drop_index("ix_analytics_events_user_key_pseudonym", table_name="analytics_events")
    op.drop_table("analytics_events")
    op.drop_index("ix_outbox_events_state", table_name="outbox_events")
    op.drop_index("ix_outbox_events_recipient_user_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_kind", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("processed_updates")
    op.drop_table("stages")
    op.drop_table("sectors")
    op.drop_index("ix_user_consents_user_id", table_name="user_consents")
    op.drop_table("user_consents")
    op.drop_table("user_profiles")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
