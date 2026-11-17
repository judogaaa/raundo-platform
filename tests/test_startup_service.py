"""Startup draft/media/submit and ownership boundaries."""

from __future__ import annotations

import pytest

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.domain import REVIEW_SUBMITTED
from raundo.services.accounts import AccountService
from raundo.services.startups import StartupService, StartupServiceError
from tests.conftest import grant_startup_pro


async def _owner(session):
    await ensure_catalog_seeded(session)
    return await AccountService(session).bind_telegram_user(
        telegram_id=101,
        username="founder",
        first_name="F",
        last_name=None,
        language_code="ru",
    )


async def _fill_minimal(session, startup, svc: StartupService) -> None:
    startup.one_liner = "x" * 50
    startup.full_description = "y" * 100
    await svc.set_primary_sector(startup, "ai_ml")
    await svc.set_stage(startup, "mvp")
    startup.website_url = "https://example.com"
    startup.intents = ["feedback"]
    startup.audience_codes = ["b2c"]
    startup.progress_codes = ["mvp"]
    startup.monetization_status = "not_yet"
    startup.fundraising_stage = "not_raising"
    startup.country = "Россия"
    startup.founder_display_name = "Founder"
    startup.founder_role = "CEO"
    await svc.add_photo(
        startup,
        telegram_file_id="file-1",
        file_unique_id="uniq-1",
    )


@pytest.mark.asyncio
async def test_submit_requires_photos_and_fields(session) -> None:
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Alpha")
    with pytest.raises(StartupServiceError) as exc:
        await svc.submit_for_review(startup, owner.id)
    assert "фото" in exc.value.message.lower() or "нельзя" in exc.value.message.lower()


@pytest.mark.asyncio
async def test_fourth_photo_rejected_and_duplicate_file(session) -> None:
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Beta")
    # Issue #5: FREE cap is 1 photo; full gallery needs Startup PRO.
    await grant_startup_pro(session, owner_user_id=owner.id, startup_id=startup.id)
    for i in range(1, 4):
        await svc.add_photo(
            startup,
            telegram_file_id=f"f{i}",
            file_unique_id=f"u{i}",
        )
    with pytest.raises(StartupServiceError):
        await svc.add_photo(startup, telegram_file_id="f4", file_unique_id="u4")
    with pytest.raises(StartupServiceError):
        await svc.add_photo(startup, telegram_file_id="f1-again", file_unique_id="u1")


@pytest.mark.asyncio
async def test_free_cap_allows_one_photo_blocks_second(session) -> None:
    """Issue #5 (D014): FREE startups may hold exactly one photo."""
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "FreeCap")
    await svc.add_photo(startup, telegram_file_id="f1", file_unique_id="u1")
    with pytest.raises(StartupServiceError) as exc:
        await svc.add_photo(startup, telegram_file_id="f2", file_unique_id="u2")
    assert "premium" in exc.value.message.lower()
    assert await svc.photo_count(startup) == 1
    # Granting PRO restores the 3-photo cap without deleting anything.
    await grant_startup_pro(session, owner_user_id=owner.id, startup_id=startup.id)
    await svc.add_photo(startup, telegram_file_id="f2", file_unique_id="u2")
    await svc.add_photo(startup, telegram_file_id="f3", file_unique_id="u3")
    assert await svc.photo_count(startup) == 3


@pytest.mark.asyncio
async def test_grandfathered_gallery_keeps_three_photos_without_pro(session) -> None:
    """Legacy galleries (ZAPAS rule): the migration flag keeps the full cap."""
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "LegacyGallery")
    startup.grandfathered_gallery = True
    await session.flush()
    for i in range(1, 4):
        await svc.add_photo(
            startup,
            telegram_file_id=f"g{i}",
            file_unique_id=f"gu{i}",
        )
    assert await svc.photo_count(startup) == 3
    with pytest.raises(StartupServiceError):
        await svc.add_photo(startup, telegram_file_id="g4", file_unique_id="gu4")


@pytest.mark.asyncio
async def test_submit_ok_then_resubmit_blocked(session) -> None:
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Gamma")
    await _fill_minimal(session, startup, svc)
    await svc.submit_for_review(startup, owner.id)
    assert startup.review_state == REVIEW_SUBMITTED
    with pytest.raises(StartupServiceError):
        await svc.submit_for_review(startup, owner.id)


@pytest.mark.asyncio
async def test_readiness_rejects_legacy_long_pitch_and_ask_not_raising(session) -> None:
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "LegacyPitch")
    await _fill_minimal(session, startup, svc)
    startup.one_liner = "L" * 200
    errs = await svc.readiness_errors(startup)
    assert any("питч" in e for e in errs)

    startup.one_liner = "x" * 50
    startup.ask_amount_minor = 1_500_000_000
    startup.ask_currency = "RUB"
    startup.fundraising_stage = "not_raising"
    errs = await svc.readiness_errors(startup)
    assert any("инвестиционный запрос" in e for e in errs)


@pytest.mark.asyncio
async def test_cannot_submit_or_load_foreign_startup(session) -> None:
    owner = await _owner(session)
    other = await AccountService(session).bind_telegram_user(
        telegram_id=202,
        username="other",
        first_name="O",
        last_name=None,
        language_code="ru",
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Delta")
    await _fill_minimal(session, startup, svc)
    assert await svc.get_owned(startup.id, other.id) is None
    with pytest.raises(StartupServiceError):
        await svc.submit_for_review(startup, other.id)


@pytest.mark.asyncio
async def test_pending_scan_state_does_not_block_count_or_submit_readiness(session) -> None:
    """No scanner/transition pending→accepted yet; preview/submit must still count photos."""
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "PendingScan")
    await _fill_minimal(session, startup, svc)
    # Identity-map may hold Startup without media collection; refresh explicitly.
    await session.refresh(startup, attribute_names=["media"])
    assert startup.media
    assert all(m.scan_state == "pending" for m in startup.media)
    assert all(m.visibility == "public" for m in startup.media)
    assert await svc.photo_count(startup) == 1
    errors = await svc.readiness_errors(startup)
    assert not any("фото" in e for e in errors)
    from raundo.cards.startup_card import build_startup_card_view

    view = build_startup_card_view(startup, sector_labels=["AI"], stage_label="MVP")
    assert view.photo_count == 1
    assert view.cover_file_id == "file-1"
    assert view.gallery_file_ids == ("file-1",)


@pytest.mark.asyncio
async def test_ensure_editable_blocks_submitted_and_keeps_photos(session) -> None:
    owner = await _owner(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "EditableGuard")
    await _fill_minimal(session, startup, svc)
    await grant_startup_pro(session, owner_user_id=owner.id, startup_id=startup.id)
    await svc.add_photo(startup, telegram_file_id="f2", file_unique_id="u2")
    await svc.add_photo(startup, telegram_file_id="f3", file_unique_id="u3")
    assert await svc.photo_count(startup) == 3
    media = await svc.get_owned(startup.id, owner.id)
    assert media is not None
    assert all(m.scan_state == "pending" for m in media.media)

    await svc.submit_for_review(startup, owner.id)
    await svc.ensure_editable(startup)
    rev = startup.current_revision
    startup.one_liner = "a" * 50
    await svc.register_owner_change(startup)
    assert startup.current_revision == rev + 1
    assert await svc.photo_count(startup) == 3
    startup.one_liner = "z" * 55  # direct ORM write possible; service path must still guard
    await session.flush()
    assert await svc.photo_count(startup) == 3
