"""Serialize approved public views for startups and investors."""

from __future__ import annotations

from typing import Any

from raundo.models.investor import InvestorProfile
from raundo.models.startup import Startup, StartupMedia


def startup_snapshot_payload(startup: Startup, media: list[StartupMedia]) -> dict[str, Any]:
    return {
        "revision": startup.current_revision,
        "name": startup.name,
        "one_liner": startup.one_liner,
        "full_description": startup.full_description,
        "primary_sector_id": str(startup.primary_sector_id) if startup.primary_sector_id else None,
        "stage_id": str(startup.stage_id) if startup.stage_id else None,
        "website_url": startup.website_url,
        "app_url": startup.app_url,
        "demo_url": startup.demo_url,
        "no_link_reason": startup.no_link_reason,
        "intents": list(startup.intents or []),
        "audience_codes": list(startup.audience_codes or []),
        "audience_note": startup.audience_note,
        "progress_codes": list(startup.progress_codes or []),
        "monetization_status": startup.monetization_status,
        "monetization_note": startup.monetization_note,
        "fundraising_stage": startup.fundraising_stage,
        "use_of_funds_codes": list(startup.use_of_funds_codes or []),
        "use_of_funds_note": startup.use_of_funds_note,
        "ask_amount_minor": startup.ask_amount_minor,
        "ask_currency": startup.ask_currency,
        "country": startup.country,
        "founder_display_name": startup.founder_display_name,
        "founder_role": startup.founder_role,
        "contact_policy": startup.contact_policy,
        "contact_username": startup.contact_username,
        "show_contact_after_accept": startup.show_contact_after_accept,
        "media": [
            {
                "telegram_file_id": row.telegram_file_id,
                "file_unique_id": row.file_unique_id,
                "position": row.position,
            }
            for row in sorted(media, key=lambda m: m.position)
        ],
    }


def investor_snapshot_payload(
    profile: InvestorProfile,
    *,
    sector_ids: list[str],
    stage_ids: list[str],
) -> dict[str, Any]:
    return {
        "revision": profile.current_revision,
        "display_name": profile.display_name,
        "investor_type": profile.investor_type,
        "organization_name": profile.organization_name,
        "position": profile.position,
        "strategy_description": profile.strategy_description,
        "support_modes": list(profile.support_modes or []),
        "min_check_minor": profile.min_check_minor,
        "max_check_minor": profile.max_check_minor,
        "currency": profile.currency,
        "check_discuss_individually": profile.check_discuss_individually,
        "geography": dict(profile.geography or {}),
        "public_reference_url": profile.public_reference_url,
        "telegram_photo_file_id": profile.telegram_photo_file_id,
        "telegram_photo_unique_id": profile.telegram_photo_unique_id,
        "contact_policy": profile.contact_policy,
        "contact_username": profile.contact_username,
        "show_contact_after_accept": profile.show_contact_after_accept,
        "sector_ids": sector_ids,
        "stage_ids": stage_ids,
    }


def strip_sensitive_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Withdraw contacts and images from the last public view immediately."""
    data = dict(snapshot)
    data.pop("telegram_photo_file_id", None)
    data.pop("telegram_photo_unique_id", None)
    data.pop("media", None)
    data["contact_policy"] = "relay"
    data.pop("contact_username", None)
    data["show_contact_after_accept"] = False
    return data
