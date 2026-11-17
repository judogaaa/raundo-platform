"""Investor and organization cards for Telegram HTML."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Sequence

from raundo.domain import SUPPORT_MODE_LABELS, cheque_is_relevant, format_cheque_range
from raundo.models.investor import InvestorProfile
from raundo.ui_labels import (
    contact_policy_label,
    geography_label,
    participant_type_plain,
    review_state_label,
    sector_labels,
    stage_labels,
)

STRATEGY_COMPACT_MAX = 280


@dataclass(frozen=True)
class InvestorCardView:
    display_name: str
    type_label: str
    organization_name: str | None
    position: str | None
    support_labels: tuple[str, ...]
    strategy: str
    sector_line: str | None
    stage_line: str | None
    cheque_line: str | None
    geography_line: str | None
    public_url: str | None
    contact_line: str | None
    photo_file_id: str | None
    review_label: str | None
    show_organization: bool


def build_investor_card_view(
    profile: InvestorProfile,
    *,
    sector_codes: Sequence[str],
    stage_codes: Sequence[str],
    include_owner_private: bool = False,
) -> InvestorCardView:
    modes = tuple(
        SUPPORT_MODE_LABELS[m] for m in (profile.support_modes or []) if m in SUPPORT_MODE_LABELS
    )
    cheque = None
    if cheque_is_relevant(profile.investor_type, profile.support_modes):
        cheque = format_cheque_range(
            profile.min_check_minor,
            profile.max_check_minor,
            profile.currency,
            discuss=profile.check_discuss_individually,
        ) or None
    org = (profile.organization_name or "").strip() or None
    show_org = bool(org)
    sectors = sector_labels(list(sector_codes))
    stages = stage_labels(list(stage_codes))
    contact = None
    if include_owner_private:
        contact = contact_policy_label(profile.contact_policy)
    review = review_state_label(profile.review_status) if include_owner_private else None
    return InvestorCardView(
        display_name=(profile.display_name or "").strip() or "Без имени",
        type_label=participant_type_plain(profile.investor_type),
        organization_name=org,
        position=(profile.position or "").strip() or None,
        support_labels=modes,
        strategy=(profile.strategy_description or "").strip(),
        sector_line=", ".join(sectors) if sectors else None,
        stage_line=", ".join(stages) if stages else None,
        cheque_line=cheque,
        geography_line=geography_label(profile.geography),
        public_url=profile.public_reference_url,
        contact_line=contact,
        photo_file_id=(profile.telegram_photo_file_id or None),
        review_label=review,
        show_organization=show_org,
    )


def _strategy_compact(text: str) -> str:
    value = text.strip()
    if len(value) <= STRATEGY_COMPACT_MAX:
        return value
    window = value[: STRATEGY_COMPACT_MAX + 1]
    cut = -1
    for sep in (". ", "! ", "? "):
        idx = window.rfind(sep)
        if idx >= int(STRATEGY_COMPACT_MAX * 0.45):
            cut = max(cut, idx + 1)
    if cut > 0:
        return value[:cut].rstrip()
    space = window.rfind(" ")
    if space >= int(STRATEGY_COMPACT_MAX * 0.4):
        return value[:space].rstrip(" ,;:")
    return value[:STRATEGY_COMPACT_MAX].rstrip()


def compact_investor_html(view: InvestorCardView, *, owner_preview: bool = False) -> str:
    lines = [f"<b>👤 {escape(view.display_name)}</b>", escape(view.type_label)]
    if view.show_organization and view.organization_name:
        lines.append(escape(view.organization_name))
        if view.position:
            lines.append(escape(view.position))
    elif view.position:
        lines.append(escape(view.position))
    if view.support_labels:
        lines.append("")
        lines.append("<b>🤝 Формат сотрудничества</b>")
        lines.append(escape(", ".join(view.support_labels)))
    if view.strategy:
        lines.append("")
        lines.append("<b>💼 Инвестиционная стратегия</b>")
        lines.append(escape(_strategy_compact(view.strategy)))
    if view.sector_line:
        lines.append("")
        lines.append("<b>🎯 Направления</b>")
        lines.append(escape(view.sector_line))
    if view.stage_line:
        lines.append("")
        lines.append("<b>📈 Стадии</b>")
        lines.append(escape(view.stage_line))
    if view.cheque_line:
        lines.append("")
        lines.append("<b>💰 Инвестиционный чек</b>")
        lines.append(escape(view.cheque_line))
    if view.geography_line:
        lines.append("")
        lines.append("<b>🌍 География</b>")
        lines.append(escape(view.geography_line))
    if view.public_url:
        lines.append("")
        lines.append("<b>🔗 Публичная ссылка</b>")
        lines.append(escape(view.public_url))
    if view.contact_line:
        lines.append("")
        lines.append("<b>🔐 Способ связи</b>")
        lines.append(escape(view.contact_line))
    if owner_preview and view.review_label:
        lines.append("")
        lines.append(escape(f"Статус: {view.review_label}"))
    return "\n".join(lines)


def details_investor_html(view: InvestorCardView, *, owner_preview: bool = False) -> str:
    lines = [f"<b>👤 {escape(view.display_name)}</b>", escape(view.type_label)]
    if view.show_organization and view.organization_name:
        lines.append("")
        lines.append("<b>🏢 Организация</b>")
        lines.append(escape(view.organization_name))
        if view.position:
            lines.append(escape(view.position))
        lines.append("Принадлежность подтверждается администрацией отдельно.")
    if view.support_labels:
        lines.append("")
        lines.append("<b>🤝 Формат сотрудничества</b>")
        lines.append(escape(", ".join(view.support_labels)))
    lines.append("")
    lines.append("<b>💼 Инвестиционная стратегия</b>")
    lines.append(escape(view.strategy) if view.strategy else "Стратегия пока не заполнена.")
    if view.sector_line:
        lines.append("")
        lines.append("<b>🎯 Направления</b>")
        lines.append(escape(view.sector_line))
    if view.stage_line:
        lines.append("")
        lines.append("<b>📈 Стадии</b>")
        lines.append(escape(view.stage_line))
    if view.cheque_line:
        lines.append("")
        lines.append("<b>💰 Инвестиционный чек</b>")
        lines.append(escape(view.cheque_line))
    if view.geography_line:
        lines.append("")
        lines.append("<b>🌍 География</b>")
        lines.append(escape(view.geography_line))
    if view.public_url:
        lines.append("")
        lines.append("<b>🔗 Публичная ссылка</b>")
        lines.append(escape(view.public_url))
    if view.contact_line:
        lines.append("")
        lines.append("<b>🔐 Способ связи</b>")
        lines.append(escape(view.contact_line))
    if owner_preview:
        lines.append("")
        lines.append("Показатели указаны участником. RAUNDO не подтверждает капитал.")
        if view.review_label:
            lines.append(escape(f"Статус: {view.review_label}"))
    return "\n".join(lines)
