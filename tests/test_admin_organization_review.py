"""Organization, representative and mandate review with audit and notifications."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from raundo.domain import REVIEW_APPROVED, REVIEW_CHANGES, REVIEW_REJECTED
from raundo.models.admin_panel import AdminAuditLog
from raundo.models.system import OutboxEvent
from raundo.services.accounts import AccountService
from raundo.services.consents import ConsentService
from raundo.services.organization_review import (
    OrganizationReviewError,
    list_organization_queue,
    organization_detail,
    review_membership,
    review_organization,
)
from raundo.services.organizations import (
    MEMBERSHIP_APPROVED,
    MEMBERSHIP_REVOKED,
    OrganizationService,
)


async def _claim(session, telegram_id: int = 55001, *, mandate: bool = True):  # noqa: ANN001
    user = await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"org{telegram_id}",
        first_name="Org",
        last_name=None,
        language_code="ru",
    )
    await ConsentService(session).grant(user.id, "profile_publication", source="test")
    org, membership = await OrganizationService(session).claim_organization(
        user_id=user.id,
        name=f"Fund {telegram_id}",
        participant_type="venture_fund",
        role="partner",
        claims_direct_investment=mandate,
    )
    return user, org, membership


@pytest.mark.asyncio
async def test_queue_and_detail_show_pending_representative(session) -> None:
    _user, org, membership = await _claim(session)
    queue = await list_organization_queue(session)
    row = next(item for item in queue if item["id"] == str(org.id))
    assert row["pending_representatives"] == 1
    detail = await organization_detail(session, org.id)
    assert detail["memberships"][0]["id"] == str(membership.id)
    assert detail["memberships"][0]["claims_direct_investment_mandate"] is True
    assert detail["can_verify_memberships"] is False


@pytest.mark.asyncio
async def test_membership_cannot_be_verified_before_organization(session) -> None:
    _user, _org, membership = await _claim(session, 55002)
    with pytest.raises(OrganizationReviewError) as exc:
        await review_membership(
            session,
            membership_id=membership.id,
            actor_id=uuid.uuid4(),
            decision="approve",
        )
    assert exc.value.code == "organization_not_approved"


@pytest.mark.asyncio
async def test_approve_organization_then_membership_with_mandate(session) -> None:
    _user, org, membership = await _claim(session, 55003)
    actor = uuid.uuid4()
    org = await review_organization(session, org_id=org.id, actor_id=actor, decision="approve")
    assert org.review_status == REVIEW_APPROVED

    membership = await review_membership(
        session,
        membership_id=membership.id,
        actor_id=actor,
        decision="approve",
        mandate=True,
    )
    assert membership.status == MEMBERSHIP_APPROVED
    assert membership.claims_direct_investment_mandate is True
    assert membership.verified_by == actor
    assert membership.verified_at is not None

    audits = (
        await session.execute(select(AdminAuditLog).order_by(AdminAuditLog.created_at))
    ).scalars()
    actions = [(a.resource_type, a.action) for a in audits]
    assert ("organization", "approve") in actions
    assert ("membership", "approve") in actions

    events = (
        await session.execute(select(OutboxEvent).where(OutboxEvent.kind.like("organization.%")))
    ).scalars()
    assert [e.kind for e in events] == ["organization.approved"]


@pytest.mark.asyncio
async def test_membership_approval_can_deny_mandate(session) -> None:
    _user, org, membership = await _claim(session, 55004)
    actor = uuid.uuid4()
    await review_organization(session, org_id=org.id, actor_id=actor, decision="approve")
    membership = await review_membership(
        session,
        membership_id=membership.id,
        actor_id=actor,
        decision="approve",
        mandate=False,
    )
    assert membership.status == MEMBERSHIP_APPROVED
    assert membership.claims_direct_investment_mandate is False


@pytest.mark.asyncio
async def test_reject_organization_requires_reason(session) -> None:
    _user, org, _membership = await _claim(session, 55005)
    with pytest.raises(OrganizationReviewError) as exc:
        await review_organization(
            session, org_id=org.id, actor_id=uuid.uuid4(), decision="reject", reason="нет"
        )
    assert exc.value.code == "reason_required"

    org = await review_organization(
        session,
        org_id=org.id,
        actor_id=uuid.uuid4(),
        decision="reject",
        reason="Организация не подтвердила статус.",
    )
    assert org.review_status == REVIEW_REJECTED


@pytest.mark.asyncio
async def test_request_changes_and_duplicate_approve_guard(session) -> None:
    _user, org, _membership = await _claim(session, 55006)
    org = await review_organization(
        session,
        org_id=org.id,
        actor_id=uuid.uuid4(),
        decision="changes",
        reason="Добавьте ссылку на реестр.",
    )
    assert org.review_status == REVIEW_CHANGES
    await review_organization(session, org_id=org.id, actor_id=uuid.uuid4(), decision="approve")
    with pytest.raises(OrganizationReviewError) as exc:
        await review_organization(
            session, org_id=org.id, actor_id=uuid.uuid4(), decision="approve"
        )
    assert exc.value.code == "not_open"


@pytest.mark.asyncio
async def test_revoke_membership_clears_mandate_and_blocks_restore(session) -> None:
    _user, org, membership = await _claim(session, 55007)
    actor = uuid.uuid4()
    await review_organization(session, org_id=org.id, actor_id=actor, decision="approve")
    await review_membership(
        session, membership_id=membership.id, actor_id=actor, decision="approve", mandate=True
    )
    membership = await review_membership(
        session,
        membership_id=membership.id,
        actor_id=actor,
        decision="revoke",
        reason="Полномочия прекращены.",
    )
    assert membership.status == MEMBERSHIP_REVOKED
    assert membership.claims_direct_investment_mandate is False
    assert membership.revoked_at is not None

    with pytest.raises(OrganizationReviewError) as exc:
        await review_membership(
            session, membership_id=membership.id, actor_id=actor, decision="approve"
        )
    assert exc.value.code == "not_open"


@pytest.mark.asyncio
async def test_official_name_is_normalized_on_approval(session) -> None:
    _user, org, _membership = await _claim(session, 55008)
    org = await review_organization(
        session,
        org_id=org.id,
        actor_id=uuid.uuid4(),
        decision="approve",
        official_name="  RAUNDO Capital Fund  ",
    )
    assert org.name == "RAUNDO Capital Fund"
    assert org.normalized_name == "raundo capital fund"
