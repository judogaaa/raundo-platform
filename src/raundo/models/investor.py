"""Investor profile, preferences and review history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from raundo.db.base import Base, TimestampMixin


class InvestorProfile(Base, TimestampMixin):
    __tablename__ = "investor_profiles"
    __table_args__ = (Index("ix_investor_profiles_deleted_at", "deleted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    investor_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Self-declared org string — does NOT prove affiliation (see organization_memberships)
    organization_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    claimed_organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="SET NULL"), nullable=True
    )
    # claimed|under_review|approved|revoked — never auto-approved from registration
    affiliation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", index=True
    )
    position: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    strategy_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    support_modes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    claims_direct_investment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    open_for_requests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_check_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_check_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    check_discuss_individually: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geography: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    public_reference_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    telegram_photo_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    telegram_photo_unique_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contact_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="relay")
    contact_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    show_contact_after_accept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seeking: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    sector_preferences: Mapped[list[InvestorSectorPreference]] = relationship(
        back_populates="investor",
        cascade="all, delete-orphan",
    )
    stage_preferences: Mapped[list[InvestorStagePreference]] = relationship(
        back_populates="investor",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[list[InvestorReview]] = relationship(
        back_populates="investor",
        cascade="all, delete-orphan",
    )


class InvestorSectorPreference(Base):
    __tablename__ = "investor_sector_preferences"

    investor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("investor_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    sector_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sectors.id", ondelete="CASCADE"), primary_key=True
    )

    investor: Mapped[InvestorProfile] = relationship(back_populates="sector_preferences")


class InvestorStagePreference(Base):
    __tablename__ = "investor_stage_preferences"

    investor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("investor_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stages.id", ondelete="CASCADE"), primary_key=True
    )

    investor: Mapped[InvestorProfile] = relationship(back_populates="stage_preferences")


class InvestorReview(Base):
    __tablename__ = "investor_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    investor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("investor_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_proofs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    investor: Mapped[InvestorProfile] = relationship(back_populates="reviews")
