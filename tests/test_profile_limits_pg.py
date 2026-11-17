"""PostgreSQL integration for per-type profile limits and Premium entitlements."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.billing import PremiumSubscription
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupMedia, StartupReview
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService
from raundo.services.organizations import OrganizationService
from raundo.services.profile_limits import (
    KIND_STARTUP,
    ProfileLimitError,
    ensure_can_create,
    limits_for_user,
)
from raundo.services.startups import StartupService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_000


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    if "raundo_local_suite" not in os.environ.get("DATABASE_URL", ""):
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 100)
            )
        ).scalars().all()
        if old:
            await session.execute(delete(PremiumSubscription).where(PremiumSubscription.user_id.in_(old)))
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(old)))
            ).scalars().all()
            if startups:
                await session.execute(delete(StartupReview).where(StartupReview.startup_id.in_(startups)))
                await session.execute(delete(StartupMedia).where(StartupMedia.startup_id.in_(startups)))
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            orgs = (
                await session.execute(
                    select(FundingOrganization.id).where(
                        FundingOrganization.created_by_user_id.in_(old)
                    )
                )
            ).scalars().all()
            if orgs:
                await session.execute(
                    delete(OrganizationMembership).where(
                        OrganizationMembership.organization_id.in_(orgs)
                    )
                )
                await session.execute(
                    delete(FundingOrganization).where(FundingOrganization.id.in_(orgs))
                )
            await session.execute(delete(InvestorProfile).where(InvestorProfile.user_id.in_(old)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _user(factory, offset: int):  # noqa: ANN001, ANN202
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + offset,
            username=f"limits_user_{offset}",
            first_name="Limits",
            last_name=str(offset),
            language_code="ru",
        )
        await session.commit()
        return user.id


async def _grant(factory, user_id, plan_code: str, *, days: int = 30, status: str = "active") -> None:  # noqa: ANN001
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(
            PremiumSubscription(
                id=uuid.uuid4(),
                owner_type="user",
                user_id=user_id,
                plan_code=plan_code,
                duration_code="30d",
                price_minor_at_purchase=0,
                currency_at_purchase="RUB",
                status=status,
                source="test_grant",
                is_test=True,
                test_reason="limits test",
                idempotency_key=f"lt:{uuid.uuid4().hex}",
                starts_at=now,
                expires_at=now + timedelta(days=days),
            )
        )
        await session.commit()


async def test_free_one_per_type_and_independence(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 1)
    async with factory() as session:
        svc = StartupService(session)
        await svc.create_draft(user, "Limit Startup One")
        await session.commit()
        with pytest.raises(ProfileLimitError):
            await svc.create_draft(user, "Limit Startup Two")
        await session.rollback()

    async with factory() as session:
        investor = await InvestorService(session).get_or_create_draft(user)
        await session.commit()
        assert investor.user_id == user
        with pytest.raises(ProfileLimitError):
            await InvestorService(session).create_additional_draft(user)
        await session.rollback()

    async with factory() as session:
        org, _membership = await OrganizationService(session).claim_organization(
            user_id=user, name="E2E Limit Fund", participant_type="vc_fund"
        )
        await session.commit()
        assert org.created_by_user_id == user
        with pytest.raises(ProfileLimitError):
            await OrganizationService(session).claim_organization(
                user_id=user, name="E2E Limit Fund Two", participant_type="vc_fund"
            )
        await session.rollback()

    async with factory() as session:
        limits = await limits_for_user(session, user)
    assert (limits.startup.used, limits.startup.limit) == (1, 1)
    assert (limits.investor.used, limits.investor.limit) == (1, 1)
    assert (limits.organization.used, limits.organization.limit) == (1, 1)


async def test_matching_premium_allows_three_total_only_for_its_type(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 2)
    async with factory() as session:
        await StartupService(session).create_draft(user, "Limit Pro Startup")
        await session.commit()
    await _grant(factory, user, "startup_pro")
    async with factory() as session:
        # Startup PRO unlocks the 2nd and 3rd startup (3 total) ...
        await StartupService(session).create_draft(user, "Limit Pro Startup Two")
        await StartupService(session).create_draft(user, "Limit Pro Startup Three")
        await session.commit()
        limits = await limits_for_user(session, user)
        assert (limits.startup.used, limits.startup.limit) == (3, 3)
        # ... but not a 4th.
        with pytest.raises(ProfileLimitError):
            await StartupService(session).create_draft(user, "Limit Pro Startup Four")
        await session.rollback()
    async with factory() as session:
        # Startup PRO does not unlock more investor profiles (FREE = 1).
        await InvestorService(session).get_or_create_draft(user)
        await session.flush()
        with pytest.raises(ProfileLimitError):
            await InvestorService(session).create_additional_draft(user)
        await session.rollback()


async def test_investor_pro_allows_three_investor_profiles(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 8)
    await _grant(factory, user, "investor_pro")
    async with factory() as session:
        await InvestorService(session).get_or_create_draft(user)
        await InvestorService(session).create_additional_draft(user)
        await InvestorService(session).create_additional_draft(user)
        await session.commit()
        limits = await limits_for_user(session, user)
        assert (limits.investor.used, limits.investor.limit) == (3, 3)
        with pytest.raises(ProfileLimitError):
            await InvestorService(session).create_additional_draft(user)
        await session.rollback()


async def test_expired_premium_does_not_unlock(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 3)
    async with factory() as session:
        await StartupService(session).create_draft(user, "Limit Expired One")
        await session.commit()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        session.add(
            PremiumSubscription(
                id=uuid.uuid4(),
                owner_type="user",
                user_id=user,
                plan_code="startup_pro",
                duration_code="30d",
                price_minor_at_purchase=0,
                currency_at_purchase="RUB",
                status="active",
                source="test_grant",
                is_test=True,
                idempotency_key=f"expired:{user}:{uuid.uuid4().hex[:6]}",
                starts_at=now - timedelta(days=60),
                expires_at=now - timedelta(days=30),
            )
        )
        await session.commit()
    async with factory() as session:
        limits = await limits_for_user(session, user)
        assert limits.startup.limit == 1
        with pytest.raises(ProfileLimitError):
            await StartupService(session).create_draft(user, "Limit Expired Two")


async def test_soft_deleted_profile_frees_slot_but_restore_is_checked(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 4)
    async with factory() as session:
        startup = await StartupService(session).create_draft(user, "Limit Deleted")
        startup.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    async with factory() as session:
        # A deleted draft no longer occupies the free slot ...
        await StartupService(session).create_draft(user, "Limit After Delete")
        await session.commit()
        limits = await limits_for_user(session, user)
        assert limits.startup.used == 1
    async with factory() as session:
        # ... but reviving an archived investor profile is quota-checked.
        profile = await InvestorService(session).get_or_create_draft(user)
        profile.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    async with factory() as session:
        await InvestorService(session).get_or_create_draft(user)  # revive, slot free
        await session.commit()
        with pytest.raises(ProfileLimitError):
            await InvestorService(session).create_additional_draft(user)
        await session.rollback()


async def test_representing_another_org_does_not_spend_own_limit(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 5)
    representative = await _user(factory, 6)
    async with factory() as session:
        await OrganizationService(session).claim_organization(
            user_id=owner, name="E2E Limit Shared Fund", participant_type="vc_fund"
        )
        await session.commit()
    async with factory() as session:
        # The representative joins the existing fund: no new organization is created.
        org, membership = await OrganizationService(session).claim_organization(
            user_id=representative,
            name="E2E Limit Shared Fund",
            participant_type="vc_fund",
        )
        await session.commit()
        assert org.created_by_user_id == owner
        assert membership.user_id == representative
        limits = await limits_for_user(session, representative)
        assert limits.organization.used == 0


async def test_concurrent_creation_does_not_exceed_limit(factory) -> None:  # noqa: ANN001
    user = await _user(factory, 7)

    async def attempt(name: str) -> str:
        async with factory() as session:
            try:
                await ensure_can_create(session, user_id=user, kind=KIND_STARTUP)
                await StartupService(session).create_draft(user, name)
                await session.commit()
                return "created"
            except ProfileLimitError:
                await session.rollback()
                return "denied"

    results = await asyncio.gather(attempt("Limit Race A"), attempt("Limit Race B"))
    assert sorted(results) == ["created", "denied"]
    async with factory() as session:
        limits = await limits_for_user(session, user)
    assert limits.startup.used == 1
