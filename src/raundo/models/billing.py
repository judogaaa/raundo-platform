"""Premium billing models (Issue #5, owner decision D014).

Four plans × three durations = 12 offers. No payments are connected yet:
subscriptions may only appear from explicit, audited test grants or a future
verified payment layer. Owners are explicit: user / startup / organization —
never a global "PRO" flag.
"""

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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Uuid

from raundo.db.base import Base, TimestampMixin


class PremiumPlanOffer(Base, TimestampMixin):
    """Catalog offer: plan × duration with versioned price history."""

    __tablename__ = "premium_plan_offers"
    __table_args__ = (
        UniqueConstraint("plan_code", "duration_code", "version", name="uq_premium_offer_version"),
        Index(
            "uq_premium_offer_active",
            "plan_code",
            "duration_code",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    duration_code: Mapped[str] = mapped_column(String(8), nullable=False)
    duration_hours: Mapped[int] = mapped_column(nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class PremiumSubscription(Base, TimestampMixin):
    """
    One subscription row = one paid (or explicitly test-granted) window.

    Owner is explicit: user_id always set; owner_type selects the binding —
    'user' (community/investor), 'startup' (founder's specific startup),
    'organization' (verified org). Entitlements are always computed from
    status + expires_at on the server, never from cached booleans.
    """

    __tablename__ = "premium_subscriptions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_premium_sub_idempotency"),
        Index(
            "uq_active_user_plan",
            "plan_code",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active' AND owner_type = 'user'"),
            sqlite_where=text("status = 'active' AND owner_type = 'user'"),
        ),
        Index(
            "uq_active_startup_plan",
            "plan_code",
            "startup_id",
            unique=True,
            postgresql_where=text("status = 'active' AND owner_type = 'startup'"),
            sqlite_where=text("status = 'active' AND owner_type = 'startup'"),
        ),
        Index(
            "uq_active_org_plan",
            "plan_code",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'active' AND owner_type = 'organization'"),
            sqlite_where=text("status = 'active' AND owner_type = 'organization'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # The acting human — always bound, even for startup/org plans.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_code: Mapped[str] = mapped_column(String(8), nullable=False)
    offer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("premium_plan_offers.id", ondelete="SET NULL"), nullable=True
    )
    # Price snapshot at grant/purchase time — catalog updates never rewrite it.
    price_minor_at_purchase: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_at_purchase: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    # active|expired|cancelled. active rows must also satisfy expires_at > now.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    # test_grant (audited dev/manual access) | future: stars, refund_void…
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="test_grant")
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    test_reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    granted_by_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PremiumEvent(Base, TimestampMixin):
    """Append-only subscription audit history (grants, expiry, seats, boosts)."""

    __tablename__ = "premium_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("premium_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    actor_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    safe_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class StartupBoost(Base):
    """One paid promotion activation window (30 min max, once per 24 h)."""

    __tablename__ = "startup_boosts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_startup_boost_idempotency"),
        Index(
            "uq_boost_active",
            "startup_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("premium_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # active|completed|cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StartupAnalyticsEvent(Base):
    """Real, privacy-safe per-startup events (feed will call this; no fakes)."""

    __tablename__ = "startup_analytics_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_key_pseudonym: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="organic")
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class UserCollection(Base, TimestampMixin):
    """Free default favorites plus paid Community PLUS named collections."""

    __tablename__ = "user_collections"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_collection_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserCollectionItem(Base, TimestampMixin):
    __tablename__ = "user_collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "startup_id", name="uq_user_collection_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user_collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(nullable=False, default=0)


class CommunityNotificationPref(Base, TimestampMixin):
    """Per-user notification preferences for the future feed radar."""

    __tablename__ = "community_notification_prefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    sector_codes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InvestorSavedSearch(Base, TimestampMixin):
    """A saved filter strategy — applied once the discovery feed exists."""

    __tablename__ = "investor_saved_searches"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_investor_saved_search_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class InvestorPipelineEntry(Base, TimestampMixin):
    """Personal deal pipeline: interested → studying → requested → talking / not_fit."""

    __tablename__ = "investor_pipeline_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", name="uq_investor_pipeline_entry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)


class InvestorPrivateNote(Base, TimestampMixin):
    """Private note, readable only by its author — never exposed to startups."""

    __tablename__ = "investor_private_notes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)


class PremiumOrgSeat(Base, TimestampMixin):
    """Team seat of an Organization PRO subscription — approved members only."""

    __tablename__ = "premium_org_seats"
    __table_args__ = (
        UniqueConstraint("subscription_id", "user_id", name="uq_premium_org_seat"),
        Index("ix_premium_org_seat_org_user", "organization_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("premium_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # active|revoked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    granted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)


class OrgStartupAssignment(Base, TimestampMixin):
    """Project assigned to an analyst inside one organization's workspace."""

    __tablename__ = "org_startup_assignments"
    __table_args__ = (
        UniqueConstraint("organization_id", "startup_id", name="uq_org_assignment_startup"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_to_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    # open|in_progress|done|dismissed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    note: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)


class TeamPrivateComment(Base, TimestampMixin):
    """Employee-only comment; never visible to startups or other orgs."""

    __tablename__ = "team_private_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)


class OrgCollection(Base, TimestampMixin):
    """Shared startup collection owned by the organization, not a person."""

    __tablename__ = "org_collections"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_org_collection_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("funding_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)


class OrgCollectionItem(Base, TimestampMixin):
    __tablename__ = "org_collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "startup_id", name="uq_org_collection_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("org_collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
