"""Organization claim never auto-approves; approved rights are not self-escalated."""

from datetime import datetime, timezone

import pytest

from raundo.services.accounts import AccountService
from raundo.services.organizations import (
    MEMBERSHIP_APPROVED,
    MEMBERSHIP_REVOKED,
    MEMBERSHIP_UNDER_REVIEW,
    OrganizationService,
    OrganizationServiceError,
)


async def _user(session, tid: int = 7001):
    return await AccountService(session).bind_telegram_user(
        telegram_id=tid,
        username=f"u{tid}",
        first_name="R",
        last_name=None,
        language_code="ru",
    )


@pytest.mark.asyncio
async def test_claim_creates_draft_org_and_claimed_membership(session) -> None:
    user = await _user(session)
    svc = OrganizationService(session)
    org, mem = await svc.claim_organization(
        user_id=user.id,
        name="Northwind VC",
        participant_type="fund",
        role="Partner",
        support_modes=["direct_equity"],
        claims_direct_investment=True,
    )
    assert org.participant_type == "vc_fund"
    assert org.review_status == "draft"
    assert mem.status == "claimed"
    assert mem.claims_direct_investment_mandate is True

    org2, mem2 = await svc.claim_organization(
        user_id=user.id,
        name="Northwind VC",
        participant_type="vc_fund",
        role="Partner",
    )
    assert org2.id == org.id
    assert mem2.id == mem.id


@pytest.mark.asyncio
async def test_approved_analyst_cannot_self_escalate_while_staying_approved(session) -> None:
    user = await _user(session, 7002)
    svc = OrganizationService(session)
    org, mem = await svc.claim_organization(
        user_id=user.id,
        name="Alpha Fund",
        participant_type="vc_fund",
        role="Analyst",
        support_modes=["direct_equity"],
        claims_direct_investment=False,
    )
    # Simulate Issue #8 admin approval of limited role
    mem.status = MEMBERSHIP_APPROVED
    mem.claims_direct_investment_mandate = False
    mem.role = "Analyst"
    mem.verified_at = datetime.now(timezone.utc)
    mem.verified_by = user.id
    await session.flush()

    _, mem2 = await svc.claim_organization(
        user_id=user.id,
        name="Alpha Fund",
        participant_type="vc_fund",
        role="Partner",
        support_modes=["direct_equity"],
        claims_direct_investment=True,
    )
    assert mem2.id == mem.id
    assert mem2.status == MEMBERSHIP_UNDER_REVIEW
    assert mem2.verified_at is None
    assert mem2.verified_by is None
    assert mem2.role == "Partner"
    assert mem2.claims_direct_investment_mandate is True
    assert "self_service_change_requires_review" in (mem2.decision_reason or "")


@pytest.mark.asyncio
async def test_approved_idempotent_reclaim_keeps_approved(session) -> None:
    user = await _user(session, 7003)
    svc = OrganizationService(session)
    _, mem = await svc.claim_organization(
        user_id=user.id,
        name="Beta Fund",
        participant_type="vc_fund",
        role="Partner",
        claims_direct_investment=True,
        support_modes=["direct_equity"],
    )
    mem.status = MEMBERSHIP_APPROVED
    mem.verified_at = datetime.now(timezone.utc)
    await session.flush()

    _, mem2 = await svc.claim_organization(
        user_id=user.id,
        name="Beta Fund",
        participant_type="vc_fund",
        role="Partner",
        claims_direct_investment=True,
        support_modes=["direct_equity"],
    )
    assert mem2.status == MEMBERSHIP_APPROVED
    assert mem2.verified_at is not None


@pytest.mark.asyncio
async def test_revoked_membership_cannot_be_restored_by_user(session) -> None:
    user = await _user(session, 7004)
    svc = OrganizationService(session)
    org, mem = await svc.claim_organization(
        user_id=user.id,
        name="Gamma Fund",
        participant_type="vc_fund",
        role="Partner",
    )
    mem.status = MEMBERSHIP_REVOKED
    mem.revoked_at = datetime.now(timezone.utc)
    await session.flush()

    with pytest.raises(OrganizationServiceError) as exc:
        await svc.claim_organization(
            user_id=user.id,
            name="Gamma Fund",
            participant_type="vc_fund",
            role="Partner",
        )
    assert "отозвана" in exc.value.message.lower()
    await session.refresh(mem)
    assert mem.status == MEMBERSHIP_REVOKED


@pytest.mark.asyncio
async def test_sync_membership_role_updates_claimed_role(session) -> None:
    user = await _user(session, 7005)
    svc = OrganizationService(session)
    org, mem = await svc.claim_organization(
        user_id=user.id,
        name="Delta Fund",
        participant_type="vc_fund",
        role=None,
    )
    assert mem.role is None
    updated = await svc.sync_membership_role(
        user_id=user.id,
        organization_id=org.id,
        role="Partner",
    )
    assert updated.role == "Partner"
    assert updated.status == "claimed"


@pytest.mark.asyncio
async def test_sync_role_on_approved_triggers_re_review(session) -> None:
    user = await _user(session, 7006)
    svc = OrganizationService(session)
    org, mem = await svc.claim_organization(
        user_id=user.id,
        name="Epsilon Fund",
        participant_type="vc_fund",
        role="Analyst",
    )
    mem.status = MEMBERSHIP_APPROVED
    mem.verified_at = datetime.now(timezone.utc)
    await session.flush()

    updated = await svc.sync_membership_role(
        user_id=user.id,
        organization_id=org.id,
        role="Managing Partner",
        claims_direct_investment=True,
    )
    assert updated.status == MEMBERSHIP_UNDER_REVIEW
    assert updated.role == "Managing Partner"
    assert updated.verified_at is None
