"""Derive next/previous wizard steps from persisted draft data."""

from __future__ import annotations

from typing import Any

from raundo.domain import cheque_is_relevant, investor_needs_organization
from raundo.state.investor import InvestorWizard
from raundo.state.startup import StartupWizard


def next_startup_step(startup: Any, *, photo_count: int) -> str:
    """Return FSM state string for the next incomplete required startup field."""
    if not startup.name:
        return StartupWizard.name.state
    if not startup.one_liner:
        return StartupWizard.pitch.state
    if not startup.full_description:
        return StartupWizard.description.state
    if startup.primary_sector_id is None:
        return StartupWizard.primary_sector.state
    if startup.stage_id is None:
        # Secondary sectors are optional; resume lands on that step so user can Done → stage.
        return StartupWizard.secondary_sectors.state
    has_link = bool(startup.website_url or startup.app_url or startup.demo_url)
    if not has_link and not startup.no_link_reason:
        return StartupWizard.links.state
    if photo_count < 1:
        return StartupWizard.photos.state
    if not startup.intents:
        return StartupWizard.intents.state
    if not (startup.audience_codes or []):
        return StartupWizard.audience.state
    if not (startup.progress_codes or []):
        return StartupWizard.progress.state
    if not startup.monetization_status:
        return StartupWizard.monetization.state
    if startup.monetization_status in {"planned", "active"} and not (
        startup.monetization_note or ""
    ).strip():
        return StartupWizard.monetization_note.state
    if "other" in (startup.audience_codes or []) and not (startup.audience_note or "").strip():
        return StartupWizard.audience_note.state
    # Investment ask is optional (Back from fundraising). Fundraising is required.
    if not startup.fundraising_stage:
        return StartupWizard.fundraising.state
    raising = startup.fundraising_stage != "not_raising" or bool(startup.ask_amount_minor)
    if raising and not (startup.use_of_funds_codes or []):
        return StartupWizard.use_of_funds.state
    if not startup.country:
        return StartupWizard.geography.state
    if not startup.founder_display_name or not startup.founder_role:
        return StartupWizard.founder_info.state
    return StartupWizard.preview.state


def previous_startup_step(current_state: str | None) -> str | None:
    order = [
        StartupWizard.name.state,
        StartupWizard.pitch.state,
        StartupWizard.description.state,
        StartupWizard.primary_sector.state,
        StartupWizard.secondary_sectors.state,
        StartupWizard.stage.state,
        StartupWizard.links.state,
        StartupWizard.photos.state,
        StartupWizard.intents.state,
        StartupWizard.audience.state,
        StartupWizard.audience_note.state,
        StartupWizard.progress.state,
        StartupWizard.monetization.state,
        StartupWizard.monetization_note.state,
        StartupWizard.investment_ask.state,
        StartupWizard.fundraising.state,
        StartupWizard.use_of_funds.state,
        StartupWizard.geography.state,
        StartupWizard.founder_info.state,
        StartupWizard.contact_policy.state,
        StartupWizard.preview.state,
        StartupWizard.premium_offer.state,
    ]
    if current_state not in order:
        return None
    idx = order.index(current_state)
    if idx <= 0:
        return StartupWizard.hub.state
    # Skip note sub-steps when going back
    prev = order[idx - 1]
    if prev in {StartupWizard.audience_note.state, StartupWizard.monetization_note.state}:
        prev = order[idx - 2] if idx >= 2 else StartupWizard.hub.state
    return prev


def next_investor_step(
    profile: Any,
    *,
    sector_codes: set[str],
    stage_codes: set[str],
) -> str:
    if not profile.display_name:
        return InvestorWizard.display_name.state
    if not profile.investor_type:
        return InvestorWizard.investor_type.state
    if not (profile.support_modes or []):
        return InvestorWizard.support_modes.state
    if investor_needs_organization(profile.investor_type) and not profile.organization_name:
        return InvestorWizard.organization.state
    if not profile.strategy_description:
        # Position is optional. After org is claimed, resume at strategy (not re-ask position).
        # Individuals without org still land on position before strategy on first pass.
        if investor_needs_organization(profile.investor_type) and profile.organization_name:
            return InvestorWizard.strategy.state
        if profile.position is not None:
            return InvestorWizard.strategy.state
        return InvestorWizard.position.state
    if not sector_codes:
        return InvestorWizard.sectors.state
    if not stage_codes:
        return InvestorWizard.stages.state
    if cheque_is_relevant(profile.investor_type, profile.support_modes):
        if not profile.check_discuss_individually:
            if profile.min_check_minor is None and profile.max_check_minor is None:
                return InvestorWizard.check_size.state
    if not profile.geography:
        return InvestorWizard.geography.state
    if not profile.public_reference_url:
        return InvestorWizard.public_link.state
    return InvestorWizard.preview.state


def previous_investor_step(current_state: str | None, profile: Any | None = None) -> str | None:
    order = [
        InvestorWizard.display_name.state,
        InvestorWizard.investor_type.state,
        InvestorWizard.support_modes.state,
        InvestorWizard.organization.state,
        InvestorWizard.position.state,
        InvestorWizard.strategy.state,
        InvestorWizard.sectors.state,
        InvestorWizard.stages.state,
        InvestorWizard.check_size.state,
        InvestorWizard.geography.state,
        InvestorWizard.public_link.state,
        InvestorWizard.photo.state,
        InvestorWizard.contact_policy.state,
        InvestorWizard.preview.state,
        InvestorWizard.premium_offer.state,
    ]
    if current_state not in order:
        return None
    idx = order.index(current_state)
    while idx > 0:
        idx -= 1
        candidate = order[idx]
        if profile is not None and candidate == InvestorWizard.organization.state:
            if not investor_needs_organization(profile.investor_type or ""):
                continue
        if profile is not None and candidate == InvestorWizard.check_size.state:
            if not cheque_is_relevant(profile.investor_type, profile.support_modes):
                continue
        return candidate
    return InvestorWizard.hub.state
