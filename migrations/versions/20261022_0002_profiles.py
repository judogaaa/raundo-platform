"""Startup and investor profile tables for Issue #2.

Revision ID: 20261022_0002
Revises: 20261022_0001
Create Date: 2026-10-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0002"
down_revision: Union[str, None] = "20261022_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "startups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=96), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("one_liner", sa.String(length=240), nullable=True),
        sa.Column("full_description", sa.Text(), nullable=True),
        sa.Column("primary_sector_id", sa.Uuid(), nullable=True),
        sa.Column("stage_id", sa.Uuid(), nullable=True),
        sa.Column("website_url", sa.String(length=512), nullable=True),
        sa.Column("app_url", sa.String(length=512), nullable=True),
        sa.Column("demo_url", sa.String(length=512), nullable=True),
        sa.Column("no_link_reason", sa.String(length=240), nullable=True),
        sa.Column("intents", sa.JSON(), nullable=False),
        sa.Column("ask_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("ask_currency", sa.String(length=8), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("founder_display_name", sa.String(length=128), nullable=True),
        sa.Column("founder_role", sa.String(length=128), nullable=True),
        sa.Column("contact_policy", sa.String(length=32), nullable=False),
        sa.Column("contact_username", sa.String(length=64), nullable=True),
        sa.Column("show_contact_after_accept", sa.Boolean(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["primary_sector_id"], ["sectors.id"]),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_startups_owner_user_id", "startups", ["owner_user_id"])
    op.create_index("ix_startups_review_state", "startups", ["review_state"])

    op.create_table(
        "startup_sectors",
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("sector_id", sa.Uuid(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("startup_id", "sector_id"),
    )

    op.create_table(
        "startup_media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=256), nullable=False),
        sa.Column("file_unique_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("scan_state", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("startup_id", "file_unique_id", name="uq_startup_media_file"),
        sa.UniqueConstraint("startup_id", "position", name="uq_startup_media_position"),
    )
    op.create_index("ix_startup_media_startup_id", "startup_media", ["startup_id"])

    op.create_table(
        "startup_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("startup_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_startup_reviews_startup_id", "startup_reviews", ["startup_id"])

    op.create_table(
        "investor_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("investor_type", sa.String(length=64), nullable=True),
        sa.Column("organization_name", sa.String(length=256), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("strategy_description", sa.Text(), nullable=True),
        sa.Column("min_check_minor", sa.BigInteger(), nullable=True),
        sa.Column("max_check_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("check_discuss_individually", sa.Boolean(), nullable=False),
        sa.Column("geography", sa.JSON(), nullable=False),
        sa.Column("public_reference_url", sa.String(length=512), nullable=True),
        sa.Column("contact_policy", sa.String(length=32), nullable=False),
        sa.Column("contact_username", sa.String(length=64), nullable=True),
        sa.Column("show_contact_after_accept", sa.Boolean(), nullable=False),
        sa.Column("seeking", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_investor_profiles_review_status", "investor_profiles", ["review_status"])

    op.create_table(
        "investor_sector_preferences",
        sa.Column("investor_id", sa.Uuid(), nullable=False),
        sa.Column("sector_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["investor_id"], ["investor_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("investor_id", "sector_id"),
    )

    op.create_table(
        "investor_stage_preferences",
        sa.Column("investor_id", sa.Uuid(), nullable=False),
        sa.Column("stage_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["investor_id"], ["investor_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("investor_id", "stage_id"),
    )

    op.create_table(
        "investor_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investor_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("requested_proofs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["investor_id"], ["investor_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_investor_reviews_investor_id", "investor_reviews", ["investor_id"])


def downgrade() -> None:
    op.drop_index("ix_investor_reviews_investor_id", table_name="investor_reviews")
    op.drop_table("investor_reviews")
    op.drop_table("investor_stage_preferences")
    op.drop_table("investor_sector_preferences")
    op.drop_index("ix_investor_profiles_review_status", table_name="investor_profiles")
    op.drop_table("investor_profiles")
    op.drop_index("ix_startup_reviews_startup_id", table_name="startup_reviews")
    op.drop_table("startup_reviews")
    op.drop_index("ix_startup_media_startup_id", table_name="startup_media")
    op.drop_table("startup_media")
    op.drop_table("startup_sectors")
    op.drop_index("ix_startups_review_state", table_name="startups")
    op.drop_index("ix_startups_owner_user_id", table_name="startups")
    op.drop_table("startups")
