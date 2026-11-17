"""Server-side guards for admin moderation decisions (Issue #8 prep).

Admin handlers must call these before approving or rejecting a review row.
"""

from __future__ import annotations

from raundo.domain import (
    REVIEW_SUBMITTED,
    REVIEW_SUPERSEDED,
    REVIEW_UNDER_REVIEW,
)
from raundo.models.investor import InvestorProfile, InvestorReview
from raundo.models.startup import Startup, StartupReview

_OPEN_FOR_ADMIN_DECISION = frozenset({REVIEW_SUBMITTED, REVIEW_UNDER_REVIEW})


class ReviewDecisionError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def validate_startup_review_decision(startup: Startup, review: StartupReview) -> None:
    if startup.deleted_at is not None:
        raise ReviewDecisionError("Проект удалён.", code="deleted")
    if review.state == REVIEW_SUPERSEDED:
        raise ReviewDecisionError("Заявка устарела.", code="superseded")
    if review.state not in _OPEN_FOR_ADMIN_DECISION:
        raise ReviewDecisionError("Заявка уже закрыта.", code="not_open")
    if review.revision != startup.current_revision:
        raise ReviewDecisionError(
            "Ревизия анкеты изменилась. Нужна актуальная заявка.",
            code="stale_revision",
        )


def validate_investor_review_decision(
    profile: InvestorProfile, review: InvestorReview
) -> None:
    if profile.deleted_at is not None:
        raise ReviewDecisionError("Анкета удалена.", code="deleted")
    if review.state == REVIEW_SUPERSEDED:
        raise ReviewDecisionError("Заявка устарела.", code="superseded")
    if review.state not in _OPEN_FOR_ADMIN_DECISION:
        raise ReviewDecisionError("Заявка уже закрыта.", code="not_open")
    if review.revision != profile.current_revision:
        raise ReviewDecisionError(
            "Ревизия анкеты изменилась. Нужна актуальная заявка.",
            code="stale_revision",
        )
