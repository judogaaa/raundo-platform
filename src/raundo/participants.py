"""Canonical funding participant types, support modes and legacy aliases."""

from __future__ import annotations

from typing import Any

# Canonical codes from docs/FUNDING_PARTICIPANTS.md
PARTICIPANT_TYPE_LABELS: dict[str, str] = {
    "angel": "👤 Бизнес-ангел",
    "private": "💼 Частный инвестор",
    "angel_group": "🤝 Клуб бизнес-ангелов",
    "syndicate": "🤝 Синдикат",
    "vc_fund": "🏦 Венчурный фонд (VC)",
    "micro_vc": "🌱 Pre-seed / Seed фонд",
    "cvc": "🏢 Корпоративный венчурный фонд (CVC)",
    "family_office": "💎 Family Office",
    "venture_studio": "🧪 Venture Studio",
    "accelerator": "🚀 Акселератор",
    "incubator": "🌿 Инкубатор",
    "corporate_partner": "🏭 Корпоративный партнёр",
    "public_fund": "🏛 Фонд развития / гос. поддержка",
    "impact_fund": "🌍 Impact / тематический фонд",
    "other": "➕ Другая организация",
}

# Legacy Issue #2 codes → canonical
LEGACY_PARTICIPANT_TYPE_MAP: dict[str, str] = {
    "fund": "vc_fund",
    "corporate": "cvc",
}

SUPPORT_MODE_LABELS: dict[str, str] = {
    "direct_equity": "Прямые инвестиции",
    "co_invest": "Co-invest / синдикат",
    "pilot": "Пилоты / закупки",
    "grant": "Гранты / субсидии",
    "program": "Программа / акселерация",
    "mentorship": "Менторство",
    "partnership": "Стратегическое партнёрство",
    "venture_building": "Venture building",
}

# Types that typically need an organization name (claim only — not verified affiliation)
ORG_NAME_REQUIRED_TYPES = frozenset(
    {
        "angel_group",
        "syndicate",
        "vc_fund",
        "micro_vc",
        "cvc",
        "family_office",
        "venture_studio",
        "accelerator",
        "incubator",
        "corporate_partner",
        "public_fund",
        "impact_fund",
        # legacy
        "fund",
        "corporate",
    }
)

# Types for which an investment cheque is required unless "discuss individually"
# (and unless support_modes exclude direct investing)
CHEQUE_RELEVANT_TYPES = frozenset(
    {
        "angel",
        "private",
        "angel_group",
        "syndicate",
        "vc_fund",
        "micro_vc",
        "cvc",
        "family_office",
        "impact_fund",
        "fund",
        "corporate",
    }
)

DIRECT_INVEST_SUPPORT_MODES = frozenset({"direct_equity", "co_invest"})

ALLOWED_CURRENCIES = frozenset({"RUB", "USD", "EUR", "GBP", "CNY", "KZT", "USDT"})


def canonical_participant_type(code: str | None) -> str | None:
    if code is None:
        return None
    return LEGACY_PARTICIPANT_TYPE_MAP.get(code, code)


def participant_type_label(code: str | None) -> str:
    canon = canonical_participant_type(code) or ""
    return PARTICIPANT_TYPE_LABELS.get(canon, code or "Участник")


def needs_organization_name(participant_type: str | None) -> bool:
    if not participant_type:
        return False
    return canonical_participant_type(participant_type) in ORG_NAME_REQUIRED_TYPES or (
        participant_type in ORG_NAME_REQUIRED_TYPES
    )


def cheque_is_relevant(participant_type: str | None, support_modes: list[str] | None) -> bool:
    """Accelerators/partners with only program/pilot modes do not need a cheque."""
    modes = set(support_modes or [])
    if modes & DIRECT_INVEST_SUPPORT_MODES:
        return True
    if modes and not (modes & DIRECT_INVEST_SUPPORT_MODES):
        return False
    if not participant_type:
        return True
    canon = canonical_participant_type(participant_type) or participant_type
    if canon in {
        "accelerator",
        "incubator",
        "corporate_partner",
        "venture_studio",
        "public_fund",
    }:
        return False
    return canon in CHEQUE_RELEVANT_TYPES or participant_type in CHEQUE_RELEVANT_TYPES


def default_support_modes_for_type(participant_type: str | None) -> list[str]:
    canon = canonical_participant_type(participant_type) or participant_type
    mapping: dict[str, list[str]] = {
        "angel": ["direct_equity"],
        "private": ["direct_equity"],
        "angel_group": ["direct_equity", "co_invest"],
        "syndicate": ["co_invest", "direct_equity"],
        "vc_fund": ["direct_equity", "co_invest"],
        "micro_vc": ["direct_equity"],
        "cvc": ["direct_equity", "pilot"],
        "family_office": ["direct_equity"],
        "venture_studio": ["venture_building", "direct_equity"],
        "accelerator": ["program", "mentorship"],
        "incubator": ["program", "mentorship"],
        "corporate_partner": ["pilot", "partnership"],
        "public_fund": ["grant"],
        "impact_fund": ["direct_equity", "grant"],
        "other": ["partnership"],
    }
    return list(mapping.get(canon or "", ["direct_equity"]))


def ranking_eligible_direct_invest_claim(profile: Any) -> bool:
    """Heuristic for Issue #4 prep — never grants verified status by itself."""
    modes = set(getattr(profile, "support_modes", None) or [])
    if modes and not (modes & DIRECT_INVEST_SUPPORT_MODES):
        return False
    return bool(getattr(profile, "claims_direct_investment", False))
