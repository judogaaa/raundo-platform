"""Admin moderation decision guards (Issue #8 contract)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.domain import REVIEW_SUBMITTED, REVIEW_SUPERSEDED
from raundo.models.investor import InvestorReview
from raundo.models.startup import StartupReview
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService
from raundo.services.review_guards import (
    ReviewDecisionError,
    validate_investor_review_decision,
    validate_startup_review_decision,
)
from raundo.services.startups import StartupService
from tests.test_investor_service import _complete as complete_investor
from tests.test_startup_service import _fill_minimal


@pytest.mark.asyncio
async def test_startup_guard_rejects_stale_revision(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=9101,
        username="g_f",
        first_name="G",
        last_name=None,
        language_code="ru",
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "GuardCo")
    await _fill_minimal(session, startup, svc)
    startup.current_revision = 2
    review = StartupReview(
        id=uuid.uuid4(),
        startup_id=startup.id,
        revision=1,
        state=REVIEW_SUBMITTED,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(review)
    await session.flush()
    with pytest.raises(ReviewDecisionError) as exc:
        validate_startup_review_decision(startup, review)
    assert exc.value.code == "stale_revision"


@pytest.mark.asyncio
async def test_startup_guard_rejects_superseded(session) -> None:
    await ensure_catalog_seeded(session)
    owner = await AccountService(session).bind_telegram_user(
        telegram_id=9102,
        username="g_f2",
        first_name="G",
        last_name=None,
        language_code="ru",
    )
    svc = StartupService(session)
    startup = await svc.create_draft(owner.id, "Guard2")
    review = StartupReview(
        id=uuid.uuid4(),
        startup_id=startup.id,
        revision=startup.current_revision,
        state=REVIEW_SUPERSEDED,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(review)
    await session.flush()
    with pytest.raises(ReviewDecisionError) as exc:
        validate_startup_review_decision(startup, review)
    assert exc.value.code == "superseded"


@pytest.mark.asyncio
async def test_investor_guard_accepts_matching_open_review(session) -> None:
    await ensure_catalog_seeded(session)
    user = await AccountService(session).bind_telegram_user(
        telegram_id=9103,
        username="g_i",
        first_name="I",
        last_name=None,
        language_code="ru",
    )
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    review = InvestorReview(
        id=uuid.uuid4(),
        investor_id=profile.id,
        revision=profile.current_revision,
        state=REVIEW_SUBMITTED,
        created_at=datetime.now(timezone.utc),
    )
    session.add(review)
    await session.flush()
    validate_investor_review_decision(profile, review)


@pytest.mark.asyncio
async def test_old_approved_review_cannot_approve_new_revision(session) -> None:
    await ensure_catalog_seeded(session)
    user = await AccountService(session).bind_telegram_user(
        telegram_id=9104,
        username="g_i2",
        first_name="I",
        last_name=None,
        language_code="ru",
    )
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(user.id)
    await complete_investor(profile, svc)
    old_review = InvestorReview(
        id=uuid.uuid4(),
        investor_id=profile.id,
        revision=7,
        state=REVIEW_SUBMITTED,
        created_at=datetime.now(timezone.utc),
    )
    session.add(old_review)
    await session.flush()
    await svc.delete_owned(profile, user.id)
    await session.refresh(old_review)
    assert old_review.state == REVIEW_SUPERSEDED
    revived = await svc.get_or_create_draft(user.id)
    assert revived.current_revision == 1
    with pytest.raises(ReviewDecisionError) as exc:
        validate_investor_review_decision(revived, old_review)
    assert exc.value.code == "superseded"
