"""Community PLUS / Investor PRO / Organization PRO service boundaries."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from raundo.models.billing import PremiumOrgSeat
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.services.accounts import AccountService
from raundo.services.community_plus import CommunityPlusError, CommunityPlusService
from raundo.services.investor_pro import InvestorProError, InvestorProService
from raundo.services.org_pro import OrgProError, OrgProService
from raundo.services.premium import PremiumService
from raundo.services.startups import StartupService

APPROVED = "approved"
CLAIMED = "claimed"
REVOKED = "revoked"


async def _user(session, telegram_id: int):
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        first_name="T",
        last_name=None,
        language_code="ru",
    )


async def _startup(session, owner, name: str):
    return await StartupService(session).create_draft(owner.id, name)


async def _grant(session, plan: str, user_id, **kwargs):
    premium = PremiumService(session)
    sub, _ = await premium.grant(
        plan_code=plan,
        duration_code="30d",
        acting_user_id=user_id,
        idempotency_key=f"g:{plan}:{uuid.uuid4()}",
        **kwargs,
    )
    return sub


async def _org(session, name: str = "Org") -> FundingOrganization:
    org = FundingOrganization(
        id=uuid.uuid4(),
        name=name,
        normalized_name=name.lower(),
        participant_type="vc",
        website_url=None,
    )
    session.add(org)
    await session.flush()
    return org


async def _member(
    session, org_id: uuid.UUID, user_id: uuid.UUID, status: str = CLAIMED
) -> OrganizationMembership:
    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org_id,
        user_id=user_id,
        role="analyst",
        status=status,
    )
    session.add(membership)
    await session.flush()
    return membership


async def _seat(
    session, sub, org_id: uuid.UUID, user_id: uuid.UUID
) -> PremiumOrgSeat:
    """Bootstrap a seat the way an admin-verified flow would."""
    seat = PremiumOrgSeat(
        id=uuid.uuid4(),
        subscription_id=sub.id,
        organization_id=org_id,
        user_id=user_id,
    )
    session.add(seat)
    await session.flush()
    return seat


# ----------------------------------------------------------- Community PLUS


@pytest.mark.asyncio
async def test_free_user_gets_default_collection_only(session) -> None:
    user = await _user(session, 401)
    svc = CommunityPlusService(session)
    default = await svc.ensure_default_collection(user.id)
    assert default.is_default
    with pytest.raises(CommunityPlusError) as exc:
        await svc.create_collection(user.id, "AI стартапы")
    assert exc.value.code == "premium_required"


@pytest.mark.asyncio
async def test_plus_user_creates_named_collections(session) -> None:
    user = await _user(session, 402)
    await _grant(session, "community_plus", user.id)
    svc = CommunityPlusService(session)
    collection = await svc.create_collection(user.id, "AI стартапы")
    startup = await _startup(session, user, "Target")
    item = await svc.add_item(collection, startup.id)
    assert item is not None
    # Idempotent add.
    assert await svc.add_item(collection, startup.id) is None
    # A stranger cannot touch someone else's collection items.
    stranger = await _user(session, 403)
    assert not await svc.remove_item(stranger.id, collection.id, item.id)
    assert await svc.remove_item(user.id, collection.id, item.id)


# ------------------------------------------------------------- Investor PRO


@pytest.mark.asyncio
async def test_saved_search_and_pipeline_require_pro(session) -> None:
    user = await _user(session, 404)
    startup = await _startup(session, user, "S")
    svc = InvestorProService(session)
    with pytest.raises(InvestorProError) as exc:
        await svc.create_saved_search(user.id, "AI pre-seed", {"sectors": ["ai_ml"]})
    assert exc.value.code == "premium_required"
    with pytest.raises(InvestorProError):
        await svc.set_pipeline_stage(user.id, startup.id, "interested")


@pytest.mark.asyncio
async def test_private_notes_scoped_to_author(session) -> None:
    investor = await _user(session, 405)
    other = await _user(session, 406)
    startup = await _startup(session, other, "NotMyStartup")
    await _grant(session, "investor_pro", investor.id)
    svc = InvestorProService(session)
    note = await svc.add_private_note(investor.id, "Сильная команда", startup_id=startup.id)
    # The author reads their own note.
    assert await svc.get_private_note(investor.id, note.id) is not None
    # Any other user — even the startup's founder — gets nothing.
    assert await svc.get_private_note(other.id, note.id) is None
    assert await svc.private_notes(other.id) == []
    own_notes = await svc.private_notes(investor.id)
    assert own_notes[0].body == "Сильная команда"


@pytest.mark.asyncio
async def test_pipeline_stage_transitions(session) -> None:
    investor = await _user(session, 407)
    founder = await _user(session, 408)
    startup = await _startup(session, founder, "Pipe")
    await _grant(session, "investor_pro", investor.id)
    svc = InvestorProService(session)
    entry = await svc.set_pipeline_stage(investor.id, startup.id, "interested")
    updated = await svc.set_pipeline_stage(investor.id, startup.id, "studying")
    assert updated.id == entry.id and updated.stage == "studying"
    with pytest.raises(InvestorProError):
        await svc.set_pipeline_stage(investor.id, startup.id, "hacked")


# ----------------------------------------------------------- Organization PRO


@pytest.mark.asyncio
async def test_org_pro_seat_requires_approved_membership(session) -> None:
    director = await _user(session, 409)
    candidate = await _user(session, 410)
    org = await _org(session, "Fund")
    await _member(session, org.id, director.id, status=APPROVED)
    await _member(session, org.id, candidate.id, status=CLAIMED)
    sub = await _grant(session, "organization_pro", director.id, organization_id=org.id)
    assert sub.organization_id == org.id  # belongs to the org, not the person
    await _seat(session, sub, org.id, director.id)
    svc = OrgProService(session)
    # Claimed affiliation never grants a seat.
    with pytest.raises(OrgProError) as exc:
        await svc.grant_seat(
            organization_id=org.id,
            target_user_id=candidate.id,
            granted_by_user_id=director.id,
        )
    assert exc.value.code == "membership_not_approved"
    # Approval (admin action in #8) unlocks the seat.
    await session.execute(
        update(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == candidate.id,
        )
        .values(status=APPROVED)
    )
    await session.flush()
    seat, created = await svc.grant_seat(
        organization_id=org.id,
        target_user_id=candidate.id,
        granted_by_user_id=director.id,
    )
    assert created
    assert await svc.active_seat(org.id, candidate.id) is not None


@pytest.mark.asyncio
async def test_seat_limit_five_enforced(session) -> None:
    owner = await _user(session, 431)
    org = await _org(session, "LimitFund")
    await _member(session, org.id, owner.id, status=APPROVED)
    await _grant(session, "organization_pro", owner.id, organization_id=org.id)
    sub = await PremiumService(session).active_subscription(
        "organization_pro", organization_id=org.id
    )
    assert sub is not None
    await _seat(session, sub, org.id, owner.id)
    svc = OrgProService(session)
    members = []
    for i in range(5):
        member = await _user(session, 440 + i)
        members.append(member)
        await _member(session, org.id, member.id, status=APPROVED)
    for member in members[:4]:
        _, created = await svc.grant_seat(
            organization_id=org.id,
            target_user_id=member.id,
            granted_by_user_id=owner.id,
        )
        assert created
    with pytest.raises(OrgProError) as exc:
        await svc.grant_seat(
            organization_id=org.id,
            target_user_id=members[4].id,
            granted_by_user_id=owner.id,
        )
    assert exc.value.code == "seat_limit"
    # Revoked seat can be re-granted within the limit.
    first = members[0]
    await svc.revoke_seat(
        organization_id=org.id,
        target_user_id=first.id,
        revoked_by_user_id=owner.id,
        reason="left team",
    )
    seat, created = await svc.grant_seat(
        organization_id=org.id,
        target_user_id=members[4].id,
        granted_by_user_id=owner.id,
    )
    assert created
    assert await svc.active_seat(org.id, first.id) is None


@pytest.mark.asyncio
async def test_self_escalation_impossible(session) -> None:
    """A mere approved member with no seat cannot grant seats to anyone."""
    director = await _user(session, 435)
    analyst = await _user(session, 436)
    org = await _org(session, "Escalation")
    await _member(session, org.id, director.id, status=APPROVED)
    await _member(session, org.id, analyst.id, status=APPROVED)
    await _grant(session, "organization_pro", director.id, organization_id=org.id)
    svc = OrgProService(session)
    with pytest.raises(OrgProError) as exc:
        await svc.grant_seat(
            organization_id=org.id,
            target_user_id=analyst.id,
            granted_by_user_id=analyst.id,
        )
    assert exc.value.code == "not_seat_holder"


@pytest.mark.asyncio
async def test_revoked_membership_loses_access_immediately(session) -> None:
    owner = await _user(session, 451)
    analyst = await _user(session, 452)
    org = await _org(session, "RevokeOrg")
    await _member(session, org.id, owner.id, status=APPROVED)
    await _member(session, org.id, analyst.id, status=APPROVED)
    await _grant(session, "organization_pro", owner.id, organization_id=org.id)
    sub_rev = await PremiumService(session).active_subscription(
        "organization_pro", organization_id=org.id
    )
    assert sub_rev is not None
    await _seat(session, sub_rev, org.id, owner.id)
    await _seat(session, sub_rev, org.id, analyst.id)
    svc = OrgProService(session)
    startup = await _startup(session, owner, "TeamTarget")
    comment = await svc.add_team_comment(
        organization_id=org.id, author_user_id=analyst.id, body="Смотрим", startup_id=startup.id
    )
    assert comment.id
    # Membership revoked → seat access dies immediately; data stays.
    await session.execute(
        update(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == org.id,
            OrganizationMembership.user_id == analyst.id,
        )
        .values(status=REVOKED)
    )
    await session.flush()
    with pytest.raises(OrgProError) as exc:
        await svc.add_team_comment(
            organization_id=org.id, author_user_id=analyst.id, body="Ещё раз"
        )
    assert exc.value.code == "no_access"
    comments = await svc.team_comments(org.id, reader_user_id=owner.id)
    assert len(comments) == 1  # data preserved, access revoked


@pytest.mark.asyncio
async def test_seat_revocation_blocks_access(session) -> None:
    owner = await _user(session, 455)
    analyst = await _user(session, 456)
    org = await _org(session, "SeatRevoke")
    await _member(session, org.id, owner.id, status=APPROVED)
    await _member(session, org.id, analyst.id, status=APPROVED)
    await _grant(session, "organization_pro", owner.id, organization_id=org.id)
    sub_sr = await PremiumService(session).active_subscription(
        "organization_pro", organization_id=org.id
    )
    assert sub_sr is not None
    await _seat(session, sub_sr, org.id, owner.id)
    await _seat(session, sub_sr, org.id, analyst.id)
    svc = OrgProService(session)
    assert await svc.revoke_seat(
        organization_id=org.id,
        target_user_id=analyst.id,
        revoked_by_user_id=owner.id,
        reason="offboarding",
    )
    with pytest.raises(OrgProError):
        await svc.team_comments(org.id, reader_user_id=analyst.id)
    # Only a current seat holder may revoke.
    with pytest.raises(OrgProError):
        await svc.revoke_seat(
            organization_id=org.id,
            target_user_id=owner.id,
            revoked_by_user_id=analyst.id,
        )


@pytest.mark.asyncio
async def test_cross_org_isolation(session) -> None:
    owner_a = await _user(session, 461)
    owner_b = await _user(session, 462)
    org_a = await _org(session, "OrgA")
    org_b = await _org(session, "OrgB")
    await _member(session, org_a.id, owner_a.id, status=APPROVED)
    await _member(session, org_b.id, owner_b.id, status=APPROVED)
    sub_a = await _grant(session, "organization_pro", owner_a.id, organization_id=org_a.id)
    await _grant(session, "organization_pro", owner_b.id, organization_id=org_b.id)
    sub_b = await PremiumService(session).active_subscription(
        "organization_pro", organization_id=org_b.id
    )
    assert sub_a is not None and sub_b is not None
    await _seat(session, sub_a, org_a.id, owner_a.id)
    await _seat(session, sub_b, org_b.id, owner_b.id)
    svc = OrgProService(session)
    # A's seat holder cannot read B's team comments.
    with pytest.raises(OrgProError):
        await svc.team_comments(org_b.id, reader_user_id=owner_a.id)
    # A cannot assign work to B's member or create a collection in B.
    startup = await _startup(session, owner_a, "Shared")
    with pytest.raises(OrgProError):
        await svc.assign_startup(
            organization_id=org_b.id,
            actor_user_id=owner_a.id,
            startup_id=startup.id,
            assigned_to_user_id=owner_b.id,
        )
    with pytest.raises(OrgProError) as exc:
        await svc.create_org_collection(
            organization_id=org_b.id, actor_user_id=owner_a.id, name="Steal"
        )
    assert exc.value.code == "no_access"


@pytest.mark.asyncio
async def test_org_expiry_keeps_data_blocks_actions(session) -> None:
    owner = await _user(session, 471)
    org = await _org(session, "ExpiryOrg")
    await _member(session, org.id, owner.id, status=APPROVED)
    sub = await _grant(session, "organization_pro", owner.id, organization_id=org.id)
    await _seat(session, sub, org.id, owner.id)
    svc = OrgProService(session)
    created = await svc.create_org_collection(
        organization_id=org.id, actor_user_id=owner.id, name="Короткий список"
    )
    assert created.name == "Короткий список"
    # Expiry: data stays, premium actions blocked.
    sub.expires_at = sub.expires_at - timedelta(days=365)
    await session.flush()
    with pytest.raises(OrgProError) as exc:
        await svc.create_org_collection(
            organization_id=org.id, actor_user_id=owner.id, name="Ещё"
        )
    assert exc.value.code == "no_access"
    collections = await svc.org_collections(org.id)
    assert [c.name for c in collections] == ["Короткий список"]


@pytest.mark.asyncio
async def test_no_org_pro_without_subscription(session) -> None:
    owner = await _user(session, 481)
    org = await _org(session, "NoSub")
    await _member(session, org.id, owner.id, status=APPROVED)
    svc = OrgProService(session)
    assert await svc.active_seat(org.id, owner.id) is None
    with pytest.raises(OrgProError):
        await svc.team_comments(org.id, reader_user_id=owner.id)
