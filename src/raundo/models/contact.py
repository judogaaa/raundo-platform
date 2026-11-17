"""Contact requests between a viewer and a startup founder (mutual consent)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from raundo.db.base import Base, TimestampMixin

REQUEST_PENDING = "pending"
REQUEST_ACCEPTED = "accepted"
REQUEST_DECLINED = "declined"
REQUEST_STATUSES = frozenset({REQUEST_PENDING, REQUEST_ACCEPTED, REQUEST_DECLINED})


class ContactRequest(Base, TimestampMixin):
    """One request per viewer/startup; declined stays declined (anti-spam)."""

    __tablename__ = "contact_requests"
    __table_args__ = (
        UniqueConstraint(
            "startup_id", "requester_user_id", name="uq_contact_request_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=REQUEST_PENDING)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
