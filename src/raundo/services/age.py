"""Self-declared age bracket and the 18+ gate for paid/investor features.

RAUNDO is available from 14. Premium and investor features require 18+. This is
an explicit self-declaration, not identity verification; no birth dates, IDs or
documents are collected.
"""

from __future__ import annotations

from datetime import datetime, timezone

from raundo.models.user import User

AGE_MINOR = "14_17"
AGE_ADULT = "18_plus"
VALID_BRACKETS: tuple[str, ...] = (AGE_MINOR, AGE_ADULT)

ADULT_REQUIRED_MESSAGE = (
    "🔞 <b>Только для 18+</b>\n\n"
    "Premium и инвесторские функции доступны с 18 лет.\n"
    "Подтвердите возраст, чтобы продолжить. Это самодекларация, а не проверка документа."
)


def is_adult(user: User) -> bool:
    return user.age_bracket == AGE_ADULT


def has_age(user: User) -> bool:
    return user.age_bracket in VALID_BRACKETS


def confirm_age(user: User, bracket: str) -> None:
    if bracket not in VALID_BRACKETS:
        raise ValueError("unknown age bracket")
    user.age_bracket = bracket
    user.age_confirmed_at = datetime.now(timezone.utc)


def age_label(bracket: str | None) -> str:
    if bracket == AGE_ADULT:
        return "18+"
    if bracket == AGE_MINOR:
        return "14-17"
    return "не указан"
