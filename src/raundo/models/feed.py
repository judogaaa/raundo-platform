"""Startup feed reactions: one active vote per user per startup per ranking period."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from raundo.db.base import Base, TimestampMixin

REACTION_LIKE = "like"
REACTION_DISLIKE = "dislike"
REACTION_SKIP = "skip"
REACTION_KINDS = frozenset({REACTION_LIKE, REACTION_DISLIKE, REACTION_SKIP})


class StartupReaction(Base, TimestampMixin):
    """A viewer's reaction to a published startup in a 14-day ranking period.

    ``skip`` is stored so the feed does not re-show the same card, but it never
    counts in rankings. Changing a reaction updates the row in place.
    """

    __tablename__ = "startup_reactions"
    __table_args__ = (
        UniqueConstraint(
            "startup_id",
            "user_id",
            "period_index",
            name="uq_startup_reaction_period",
        ),
        Index("ix_startup_reactions_period_kind", "period_index", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    period_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
