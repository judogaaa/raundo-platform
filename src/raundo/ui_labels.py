"""User-facing labels for stored codes. Never print raw review/contact/catalog codes."""

from __future__ import annotations

from typing import Any, Mapping

from raundo import ui
from raundo.catalog_seed import SECTORS, STAGES
from raundo.domain import CONTACT_POLICY_LABELS, REVIEW_DRAFT
from raundo.participants import (
    PARTICIPANT_TYPE_LABELS,
    canonical_participant_type,
)

# Review labels live in raundo.ui.REVIEW_STATUS_LABELS only; this module
# re-exports the same wording so wizards and cards never disagree.

AFFILIATION_LABELS: dict[str, str] = {
    "none": "",
    "claimed": "Заявлена, ожидает проверки",
    "under_review": "На проверке",
    "approved": "Подтверждена",
    "revoked": "Отклонена",
}

GEOGRAPHY_SCOPE_LABELS: dict[str, str] = {
    "ru": "Россия",
    "world": "Весь мир",
}

_SECTOR_BY_CODE = {code: name_ru for code, name_ru, _en in SECTORS}
_STAGE_BY_CODE = {code: label for code, label, _order in STAGES}

# Compact inline labels. Full names stay in prompts and cards.
PARTICIPANT_TYPE_BUTTON_LABELS: dict[str, str] = {
    "angel": "Бизнес-ангел",
    "private": "Частный инвестор",
    "angel_group": "Клуб ангелов",
    "syndicate": "Синдикат",
    "vc_fund": "Венчурный фонд",
    "micro_vc": "Seed фонд",
    "cvc": "CVC",
    "family_office": "Family Office",
    "venture_studio": "Venture Studio",
    "accelerator": "Акселератор",
    "incubator": "Инкубатор",
    "corporate_partner": "Корп. партнёр",
    "public_fund": "Фонд развития",
    "impact_fund": "Impact фонд",
    "other": "Другая орг.",
}


def review_state_label(code: str | None) -> str:
    if not code:
        return ui.status(REVIEW_DRAFT)
    return ui.status(code)


def affiliation_label(code: str | None) -> str:
    if not code or code == "none":
        return ""
    return AFFILIATION_LABELS.get(code, "")


def geography_label(value: Mapping[str, Any] | str | None) -> str:
    if value is None:
        return "Не указана"
    if isinstance(value, str):
        text = value.strip()
        return GEOGRAPHY_SCOPE_LABELS.get(text, text or "Не указана")
    scope = str(value.get("scope") or "").strip()
    if scope in GEOGRAPHY_SCOPE_LABELS:
        return GEOGRAPHY_SCOPE_LABELS[scope]
    if value.get("country"):
        return str(value["country"]).strip()
    return "Не указана"


def sector_label(code: str) -> str:
    return _SECTOR_BY_CODE.get(code, "")


def stage_label(code: str) -> str:
    return _STAGE_BY_CODE.get(code, "")


def sector_labels(codes: list[str] | set[str]) -> list[str]:
    return [label for code in codes if (label := sector_label(code))]


def stage_labels(codes: list[str] | set[str]) -> list[str]:
    return [label for code in codes if (label := stage_label(code))]


def contact_policy_label(code: str | None) -> str:
    if not code:
        return CONTACT_POLICY_LABELS["relay"]
    return CONTACT_POLICY_LABELS.get(code, CONTACT_POLICY_LABELS["relay"])


def participant_type_plain(code: str | None) -> str:
    canon = canonical_participant_type(code) or ""
    compact = PARTICIPANT_TYPE_BUTTON_LABELS.get(canon)
    if compact:
        return compact
    raw = PARTICIPANT_TYPE_LABELS.get(canon, "")
    return raw.lstrip("👤💼🤝🏦🌱🏢💎🧪🚀🌿🏭🏛🌍➕ ").strip() or "Участник"


def not_editable_message(review_code: str) -> str:
    return (
        f"Анкета в статусе «{review_state_label(review_code)}» "
        "и сейчас не может быть изменена."
    )
