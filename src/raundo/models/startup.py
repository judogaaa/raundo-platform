"""Startup listing, media gallery and review history."""

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from raundo.db.base import Base, TimestampMixin


class Startup(Base, TimestampMixin):
    __tablename__ = "startups"
    __table_args__ = (Index("ix_startups_deleted_at", "deleted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    one_liner: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    full_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_sector_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("sectors.id"), nullable=True
    )
    stage_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("stages.id"), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    app_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    demo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    no_link_reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    intents: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    audience_codes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    audience_note: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    progress_codes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    monetization_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    monetization_note: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    fundraising_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    use_of_funds_codes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    use_of_funds_note: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    ask_amount_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ask_currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    founder_display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    founder_role: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    contact_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="relay")
    contact_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    show_contact_after_accept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Issue #5: startups created before FREE=1 photo cap keep their full
    # gallery. Set explicitly by migration 20261026_0005 (>=2 photos) — never
    # inferred from current photo count.
    grandfathered_gallery: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    media: Mapped[list[StartupMedia]] = relationship(
        back_populates="startup",
        cascade="all, delete-orphan",
        order_by="StartupMedia.position",
    )
    sectors: Mapped[list[StartupSector]] = relationship(
        back_populates="startup",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[list[StartupReview]] = relationship(
        back_populates="startup",
        cascade="all, delete-orphan",
    )


class StartupSector(Base):
    __tablename__ = "startup_sectors"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True
    )
    sector_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sectors.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    startup: Mapped[Startup] = relationship(back_populates="sectors")


class StartupMedia(Base, TimestampMixin):
    __tablename__ = "startup_media"
    __table_args__ = (
        UniqueConstraint("startup_id", "position", name="uq_startup_media_position"),
        UniqueConstraint("startup_id", "file_unique_id", name="uq_startup_media_file"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="photo")
    mime: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scan_state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="public")

    startup: Mapped[Startup] = relationship(back_populates="media")


class StartupReview(Base):
    __tablename__ = "startup_reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    startup: Mapped[Startup] = relationship(back_populates="reviews")
