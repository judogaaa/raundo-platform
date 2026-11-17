"""Edit, re-moderation, delete, and public visibility for profiles."""

from __future__ import annotations

import pytest

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.domain import (
    REVIEW_APPROVED,
    REVIEW_ARCHIVED,
    REVIEW_DRAFT,
    REVIEW_PENDING_CHANGES,
    REVIEW_SUBMITTED,
    REVIEW_SUPERSEDED,
    is_investor_publicly_visible,
    is_startup_publicly_visible,
)
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService
from raundo.services.startups import StartupService, StartupServiceError
from tests.test_investor_service import _complete as complete_investor
from tests.test_startup_service import _fill_minimal


async def _founder(session):
    await ensure_catalog_seeded(session)
    return await AccountService(session).bind_telegram_user(
        telegram_id=9001,
        username="founder_lc",
        first_name="F",
        last_name=None,
        language_code="ru",
    )


async def _investor_user(session, tid: int = 9002):
    await ensure_catalog_seeded(session)
    return await AccountService(session).bind_telegram_user(
        telegram_id=tid,
        username="inv_lc",
        first_name="I",
        last_name=None,
        language_code="ru",
    )


@pytest.mark.asyncio
async def test_approved_startup_open_editor_without_write_stays_approved(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "NoTouch")
    await _fill_minimal(session, startup, svc)
    startup.review_state = REVIEW_APPROVED
    rev = startup.current_revision
    await session.flush()
    await svc.ensure_editable(startup)
    await session.flush()
    assert startup.review_state == REVIEW_APPROVED
    assert startup.current_revision == rev
    assert startup.published_snapshot is None
    assert is_startup_publicly_visible(
        review_state=startup.review_state,
        deleted_at=startup.deleted_at,
        published_snapshot=startup.published_snapshot,
    )


@pytest.mark.asyncio
async def test_approved_startup_edit_moves_to_pending_with_snapshot(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "EditCo")
    await _fill_minimal(session, startup, svc)
    startup.review_state = REVIEW_APPROVED
    await session.flush()
    await svc.prepare_owner_field_write(startup)
    startup.name = "EditCo 2"
    await session.flush()
    assert startup.review_state == REVIEW_PENDING_CHANGES
    assert startup.published_snapshot is not None
    assert startup.published_snapshot["name"] == "EditCo"


@pytest.mark.asyncio
async def test_submitted_startup_edit_bumps_revision_and_supersedes_review(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "RevCo")
    await _fill_minimal(session, startup, svc)
    await svc.submit_for_review(startup, owner.id)
    rev1 = startup.current_revision
    startup.one_liner = "z" * 50
    await svc.register_owner_change(startup)
    await session.refresh(startup, attribute_names=["reviews"])
    assert startup.current_revision == rev1 + 1
    reviews = list(startup.reviews)
    assert any(r.state == REVIEW_SUPERSEDED for r in reviews)
    assert any(r.revision == startup.current_revision and r.state == REVIEW_SUBMITTED for r in reviews)


@pytest.mark.asyncio
async def test_pending_changes_resubmit_increments_revision(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Resubmit")
    await _fill_minimal(session, startup, svc)
    startup.review_state = REVIEW_PENDING_CHANGES
    startup.published_snapshot = {"name": "Resubmit"}
    rev = startup.current_revision
    await svc.submit_for_review(startup, owner.id)
    assert startup.review_state == REVIEW_SUBMITTED
    assert startup.current_revision == rev + 1


@pytest.mark.asyncio
async def test_soft_delete_startup_hides_from_owner_list(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Gone")
    await svc.delete_owned(startup, owner.id)
    assert startup.review_state == REVIEW_ARCHIVED
    assert startup.deleted_at is not None
    items = await svc.list_for_owner(owner.id)
    assert items == []
    assert await svc.get_owned(startup.id, owner.id) is None


@pytest.mark.asyncio
async def test_cannot_delete_foreign_startup(session) -> None:
    owner = await _founder(session)
    other = await AccountService(session).bind_telegram_user(
        telegram_id=9003,
        username="other",
        first_name="O",
        last_name=None,
        language_code="ru",
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Mine")
    with pytest.raises(StartupServiceError):
        await svc.delete_owned(startup, other.id)


@pytest.mark.asyncio
async def test_public_visibility_rules(session) -> None:
    assert is_startup_publicly_visible(
        review_state=REVIEW_APPROVED, deleted_at=None, published_snapshot=None
    )
    assert not is_startup_publicly_visible(
        review_state=REVIEW_SUBMITTED, deleted_at=None, published_snapshot=None
    )
    assert is_startup_publicly_visible(
        review_state=REVIEW_PENDING_CHANGES,
        deleted_at=None,
        published_snapshot={"name": "x"},
    )
    assert not is_startup_publicly_visible(
        review_state=REVIEW_APPROVED, deleted_at="now", published_snapshot=None
    )


@pytest.mark.asyncio
async def test_approved_investor_open_editor_without_write_stays_approved(session) -> None:
    user = await _investor_user(session, 9010)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    profile.review_status = REVIEW_APPROVED
    rev = profile.current_revision
    await session.flush()
    await svc.ensure_editable(profile)
    await session.flush()
    assert profile.review_status == REVIEW_APPROVED
    assert profile.current_revision == rev
    assert profile.published_snapshot is None


@pytest.mark.asyncio
async def test_investor_revive_after_delete_clears_photo_and_approval(session) -> None:
    user = await _investor_user(session, 9011)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    await svc.set_photo(
        profile,
        telegram_file_id="old_file",
        file_unique_id="old_unique",
    )
    profile.review_status = REVIEW_APPROVED
    old_id = profile.id
    await session.flush()
    await svc.delete_owned(profile, user.id)
    revived = await svc.get_or_create_draft(user.id)
    assert revived.id == old_id
    assert revived.telegram_photo_file_id is None
    assert revived.telegram_photo_unique_id is None
    assert revived.review_status == REVIEW_DRAFT
    assert revived.approved_at is None
    assert revived.current_revision == 1
    sector_codes, stage_codes = await svc.preference_codes(revived)
    assert sector_codes == set()
    assert stage_codes == set()


@pytest.mark.asyncio
async def test_startup_recreate_after_delete_is_new_public_identity(session) -> None:
    owner = await _founder(session)
    svc = StartupService(session)
    first = await svc.create_draft(owner.id, "FirstLife")
    await _fill_minimal(session, first, svc)
    first.review_state = REVIEW_APPROVED
    old_id = first.id
    await session.flush()
    await svc.delete_owned(first, owner.id)
    second = await svc.create_draft(owner.id, "SecondLife")
    assert second.id != old_id
    assert second.review_state == REVIEW_DRAFT
    assert not is_startup_publicly_visible(
        review_state=first.review_state,
        deleted_at=first.deleted_at,
        published_snapshot=first.published_snapshot,
    )


@pytest.mark.asyncio
async def test_investor_delete_and_revive_draft(session) -> None:
    user = await _investor_user(session)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    await svc.delete_owned(profile, user.id)
    assert profile.deleted_at is not None
    revived = await svc.get_or_create_draft(user.id)
    assert revived.deleted_at is None
    assert revived.review_status == REVIEW_DRAFT


@pytest.mark.asyncio
async def test_investor_approved_edit_pending(session) -> None:
    user = await _investor_user(session, 9004)
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    profile.review_status = REVIEW_APPROVED
    await session.flush()
    await svc.prepare_owner_field_write(profile)
    profile.display_name = "New Name"
    await session.flush()
    assert profile.review_status == REVIEW_PENDING_CHANGES
    assert profile.published_snapshot is not None
    assert is_investor_publicly_visible(
        review_status=profile.review_status,
        deleted_at=None,
        published_snapshot=profile.published_snapshot,
    )
