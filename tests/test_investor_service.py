"""Investor preferences persistence and submit gates."""

from __future__ import annotations

import pytest

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.domain import REVIEW_PENDING_CHANGES, REVIEW_SUBMITTED
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService, InvestorServiceError


async def _user(session, tid: int = 303):
    await ensure_catalog_seeded(session)
    return await AccountService(session).bind_telegram_user(
        telegram_id=tid,
        username="inv",
        first_name="I",
        last_name=None,
        language_code="ru",
    )


async def _complete(profile, svc: InvestorService) -> None:
    profile.display_name = "Investor"
    profile.investor_type = "angel"
    profile.support_modes = ["direct_equity"]
    profile.strategy_description = "s" * 100
    await svc.set_sectors(profile, {"ai_ml", "fintech"})
    await svc.set_stages(profile, {"mvp", "revenue"})
    profile.check_discuss_individually = True
    profile.geography = {"scope": "ru"}
    profile.public_reference_url = "https://example.com/me"
    profile.contact_policy = "relay"
    svc.sync_mandate_flags(profile)


@pytest.mark.asyncio
async def test_sectors_stages_required_and_persisted(session) -> None:
    user = await _user(session)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    with pytest.raises(InvestorServiceError):
        await svc.set_sectors(profile, set())
    await svc.set_sectors(profile, {"ai_ml"})
    await svc.set_stages(profile, {"mvp"})
    sectors, stages = await svc.preference_codes(profile)
    assert sectors == {"ai_ml"}
    assert stages == {"mvp"}


@pytest.mark.asyncio
async def test_submit_then_resubmit_after_pending_changes(session) -> None:
    user = await _user(session)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await _complete(profile, svc)
    await svc.submit_for_review(profile, user.id)
    assert profile.review_status == REVIEW_SUBMITTED
    with pytest.raises(InvestorServiceError):
        await svc.submit_for_review(profile, user.id)
    profile.review_status = REVIEW_PENDING_CHANGES
    profile.published_snapshot = {"revision": 1}
    await svc.submit_for_review(profile, user.id)
    assert profile.review_status == REVIEW_SUBMITTED


@pytest.mark.asyncio
async def test_foreign_user_cannot_submit(session) -> None:
    user = await _user(session, 303)
    other = await _user(session, 404)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await _complete(profile, svc)
    with pytest.raises(InvestorServiceError):
        await svc.submit_for_review(profile, other.id)
    assert await svc.get_owned(profile.id, other.id) is None


@pytest.mark.asyncio
async def test_accelerator_program_skips_cheque_requirement(session) -> None:
    user = await _user(session, 505)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    profile.display_name = "Accel"
    profile.investor_type = "accelerator"
    profile.support_modes = ["program", "mentorship"]
    profile.organization_name = "Lab"
    profile.strategy_description = "s" * 100
    await svc.set_sectors(profile, {"ai_ml"})
    await svc.set_stages(profile, {"mvp"})
    profile.geography = {"scope": "world"}
    profile.public_reference_url = "https://example.com/a"
    profile.contact_policy = "relay"
    # no cheque set
    errors = svc.readiness_errors(profile, {"ai_ml"}, {"mvp"})
    assert errors == []
    await svc.submit_for_review(profile, user.id)
    assert profile.review_status == REVIEW_SUBMITTED


@pytest.mark.asyncio
async def test_investor_photo_replace_does_not_verify_org(session) -> None:
    user = await _user(session, 606)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await svc.set_photo(profile, telegram_file_id="file-1", file_unique_id="uid-1")
    assert profile.telegram_photo_file_id == "file-1"
    await svc.set_photo(profile, telegram_file_id="file-2", file_unique_id="uid-2")
    assert profile.telegram_photo_file_id == "file-2"
    assert profile.affiliation_status == "none"
