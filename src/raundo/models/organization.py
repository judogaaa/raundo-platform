"""Funding organizations and representative memberships (Issue #2 prep for #8/#12).

Claiming an organization name never auto-verifies affiliation or investment mandate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from raundo.db.base import Base, TimestampMixin


class FundingOrganization(Base, TimestampMixin):
    """Claimed/catalogued funding or support organization — not auto-verified."""

    __tablename__ = "funding_organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    participant_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    support_modes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    open_for_requests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # draft|submitted|under_review|approved|rejected|suspended — never auto-approved
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrganizationMembership(Base, TimestampMixin):
    """Link between a Telegram user and an organization — starts as claimed only."""

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # claimed|under_review|approved|revoked — Issue #8 decides approval
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="claimed", index=True)
    # Self-declared; admin must confirm separately for ranking eligibility
    claims_direct_investment_mandate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[FundingOrganization] = relationship(back_populates="memberships")
