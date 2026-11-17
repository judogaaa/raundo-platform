"""Premium billing: 4 plans × 3 durations, subscriptions, boosts, team seats.

Revision ID: 20261026_0005
Revises: 20261023_0004
Create Date: 2026-10-26

Owner decision D014 (2026-10-26): four plans with 3/7/30-day windows.
No payments are connected — subscriptions only from audited test grants or a
future verified payment layer. Legacy startups (>= 2 photos at migration time)
get grandfathered_gallery = true and keep their full gallery (ZAPAS included);
original media rows and file_ids are never modified.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from raundo.premium_catalog import PRICE_CURRENCY, all_offers

revision: str = "20261026_0005"
down_revision: Union[str, None] = "20261023_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # --- Catalog: versioned offers, one active per (plan, duration) ---
    op.create_table(
        "premium_plan_offers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("duration_code", sa.String(length=8), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_ts_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_code", "duration_code", "version", name="uq_premium_offer_version"),
    )
    op.create_index("ix_premium_plan_offers_plan_code", "premium_plan_offers", ["plan_code"])
    op.create_index("ix_premium_plan_offers_is_active", "premium_plan_offers", ["is_active"])
    op.create_index(
        "uq_premium_offer_active",
        "premium_plan_offers",
        ["plan_code", "duration_code"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    premium_plan_offers = sa.table(
        "premium_plan_offers",
        sa.column("id", sa.Uuid),
        sa.column("plan_code", sa.String),
        sa.column("duration_code", sa.String),
        sa.column("duration_hours", sa.Integer),
        sa.column("price_minor", sa.BigInteger),
        sa.column("currency", sa.String),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    import uuid as _uuid

    offer_rows = [
        {
            "id": _uuid.uuid4(),
            "plan_code": plan,
            "duration_code": duration,
            "duration_hours": hours,
            "price_minor": price,
            "currency": PRICE_CURRENCY,
            "version": 1,
            "is_active": True,
        }
        for plan, duration, hours, price in all_offers()
    ]
    op.bulk_insert(premium_plan_offers, offer_rows)

    # --- Subscriptions and audit ledger ---
    op.create_table(
        "premium_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("duration_code", sa.String(length=8), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("price_minor_at_purchase", sa.BigInteger(), nullable=False),
        sa.Column("currency_at_purchase", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="test_grant"),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("test_reason", sa.String(length=240), nullable=True),
        sa.Column("granted_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_id"], ["premium_plan_offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_premium_sub_idempotency"),
    )
    op.create_index("ix_premium_subscriptions_owner_type", "premium_subscriptions", ["owner_type"])
    op.create_index("ix_premium_subscriptions_user_id", "premium_subscriptions", ["user_id"])
    op.create_index("ix_premium_subscriptions_startup_id", "premium_subscriptions", ["startup_id"])
    op.create_index("ix_premium_subscriptions_organization_id", "premium_subscriptions", ["organization_id"])
    op.create_index("ix_premium_subscriptions_status", "premium_subscriptions", ["status"])
    op.create_index("ix_premium_subscriptions_expires_at", "premium_subscriptions", ["expires_at"])
    op.create_index(
        "uq_active_user_plan",
        "premium_subscriptions",
        ["plan_code", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND owner_type = 'user'"),
    )
    op.create_index(
        "uq_active_startup_plan",
        "premium_subscriptions",
        ["plan_code", "startup_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND owner_type = 'startup'"),
    )
    op.create_index(
        "uq_active_org_plan",
        "premium_subscriptions",
        ["plan_code", "organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND owner_type = 'organization'"),
    )

    op.create_table(
        "premium_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=240), nullable=True),
        sa.Column("safe_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["premium_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_premium_events_subscription_id", "premium_events", ["subscription_id"])
    op.create_index("ix_premium_events_event_type", "premium_events", ["event_type"])

    # --- Startup PRO: boosts and per-startup analytics foundation ---
    op.create_table(
        "startup_boosts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["premium_subscriptions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_startup_boost_idempotency"),
    )
    op.create_index("ix_startup_boosts_startup_id", "startup_boosts", ["startup_id"])
    op.create_index("ix_startup_boosts_status", "startup_boosts", ["status"])
    op.create_index(
        "uq_boost_active",
        "startup_boosts",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "startup_analytics_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("user_key_pseudonym", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="organic"),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_startup_analytics_events_startup_id", "startup_analytics_events", ["startup_id"]
    )
    op.create_index(
        "ix_startup_analytics_events_event_type", "startup_analytics_events", ["event_type"]
    )
    op.create_index(
        "ix_startup_analytics_events_created_at", "startup_analytics_events", ["created_at"]
    )

    # --- Community PLUS foundation ---
    op.create_table(
        "user_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_user_collection_name"),
    )
    op.create_index("ix_user_collections_user_id", "user_collections", ["user_id"])

    op.create_table(
        "user_collection_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["collection_id"], ["user_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "startup_id", name="uq_user_collection_item"),
    )
    op.create_index("ix_user_collection_items_collection_id", "user_collection_items", ["collection_id"])
    op.create_index("ix_user_collection_items_startup_id", "user_collection_items", ["startup_id"])

    op.create_table(
        "community_notification_prefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("sector_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- Investor PRO foundation ---
    op.create_table(
        "investor_saved_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_investor_saved_search_name"),
    )
    op.create_index("ix_investor_saved_searches_user_id", "investor_saved_searches", ["user_id"])

    op.create_table(
        "investor_pipeline_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("note", sa.String(length=240), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "startup_id", name="uq_investor_pipeline_entry"),
    )
    op.create_index("ix_investor_pipeline_entries_user_id", "investor_pipeline_entries", ["user_id"])
    op.create_index("ix_investor_pipeline_entries_startup_id", "investor_pipeline_entries", ["startup_id"])

    op.create_table(
        "investor_private_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investor_private_notes_user_id", "investor_private_notes", ["user_id"])
    op.create_index("ix_investor_private_notes_startup_id", "investor_private_notes", ["startup_id"])

    # --- Organization PRO: seats and team workspace ---
    op.create_table(
        "premium_org_seats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=240), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["premium_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subscription_id", "user_id", name="uq_premium_org_seat"),
    )
    op.create_index("ix_premium_org_seats_subscription_id", "premium_org_seats", ["subscription_id"])
    op.create_index("ix_premium_org_seats_status", "premium_org_seats", ["status"])
    op.create_index(
        "ix_premium_org_seat_org_user", "premium_org_seats", ["organization_id", "user_id"]
    )

    op.create_table(
        "org_startup_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_to_user_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("note", sa.String(length=240), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "startup_id", name="uq_org_assignment_startup"),
    )
    op.create_index(
        "ix_org_startup_assignments_organization_id",
        "org_startup_assignments",
        ["organization_id"],
    )
    op.create_index(
        "ix_org_startup_assignments_startup_id", "org_startup_assignments", ["startup_id"]
    )

    op.create_table(
        "team_private_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=True),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_private_comments_organization_id", "team_private_comments", ["organization_id"]
    )
    op.create_index(
        "ix_team_private_comments_startup_id", "team_private_comments", ["startup_id"]
    )

    op.create_table(
        "org_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_org_collection_name"),
    )
    op.create_index("ix_org_collections_organization_id", "org_collections", ["organization_id"])

    op.create_table(
        "org_collection_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["collection_id"], ["org_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id", "startup_id", name="uq_org_collection_item"),
    )
    op.create_index("ix_org_collection_items_collection_id", "org_collection_items", ["collection_id"])
    op.create_index("ix_org_collection_items_startup_id", "org_collection_items", ["startup_id"])

    # --- Legacy gallery rule (Issue #5 / ZAPAS compatibility) ---
    # Explicit flag set once at migration time; never inferred from current
    # photo count. Existing >= 2-photo startups keep the full gallery.
    op.add_column(
        "startups",
        sa.Column(
            "grandfathered_gallery",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE startups SET grandfathered_gallery = true "
        "WHERE (SELECT COUNT(*) FROM startup_media "
        "WHERE startup_media.startup_id = startups.id AND startup_media.kind = 'photo') >= 2"
    )


def downgrade() -> None:
    op.drop_column("startups", "grandfathered_gallery")
    for table in (
        "org_collection_items",
        "org_collections",
        "team_private_comments",
        "org_startup_assignments",
        "premium_org_seats",
        "investor_private_notes",
        "investor_pipeline_entries",
        "investor_saved_searches",
        "community_notification_prefs",
        "user_collection_items",
        "user_collections",
        "startup_analytics_events",
        "startup_boosts",
        "premium_events",
        "premium_subscriptions",
        "premium_plan_offers",
    ):
        op.drop_table(table)
