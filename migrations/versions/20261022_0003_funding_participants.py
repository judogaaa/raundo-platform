"""Funding organizations, memberships and expanded investor profile fields.

Revision ID: 20261022_0003
Revises: 20261022_0002
Create Date: 2026-10-22

Preserves legacy investor_type values (fund, corporate, …); canonical mapping
is applied in application code. No auto-approval of affiliation or mandate.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0003"
down_revision: Union[str, None] = "20261022_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "funding_organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("participant_type", sa.String(length=64), nullable=False),
        sa.Column("website_url", sa.String(length=512), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("support_modes", sa.JSON(), nullable=False),
        sa.Column("open_for_requests", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_funding_organizations_normalized_name", "funding_organizations", ["normalized_name"])
    op.create_index("ix_funding_organizations_participant_type", "funding_organizations", ["participant_type"])
    op.create_index("ix_funding_organizations_review_status", "funding_organizations", ["review_status"])

    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="claimed"),
        sa.Column(
            "claims_direct_investment_mandate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["funding_organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership_user"),
    )
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_status", "organization_memberships", ["status"])

    op.add_column(
        "investor_profiles",
        sa.Column("claimed_organization_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "investor_profiles",
        sa.Column(
            "affiliation_status",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "investor_profiles",
        sa.Column("support_modes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "investor_profiles",
        sa.Column(
            "claims_direct_investment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "investor_profiles",
        sa.Column(
            "open_for_requests",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_foreign_key(
        "fk_investor_profiles_claimed_organization_id",
        "investor_profiles",
        "funding_organizations",
        ["claimed_organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_investor_profiles_affiliation_status",
        "investor_profiles",
        ["affiliation_status"],
    )

    # Normalize empty support_modes JSON default for Postgres
    op.execute(
        "UPDATE investor_profiles SET support_modes = '[]'::json "
        "WHERE support_modes IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_investor_profiles_affiliation_status", table_name="investor_profiles")
    op.drop_constraint(
        "fk_investor_profiles_claimed_organization_id",
        "investor_profiles",
        type_="foreignkey",
    )
    op.drop_column("investor_profiles", "open_for_requests")
    op.drop_column("investor_profiles", "claims_direct_investment")
    op.drop_column("investor_profiles", "support_modes")
    op.drop_column("investor_profiles", "affiliation_status")
    op.drop_column("investor_profiles", "claimed_organization_id")

    op.drop_index("ix_organization_memberships_status", table_name="organization_memberships")
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_index("ix_organization_memberships_organization_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    op.drop_index("ix_funding_organizations_review_status", table_name="funding_organizations")
    op.drop_index("ix_funding_organizations_participant_type", table_name="funding_organizations")
    op.drop_index("ix_funding_organizations_normalized_name", table_name="funding_organizations")
    op.drop_table("funding_organizations")
