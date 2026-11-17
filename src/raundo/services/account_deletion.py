"""Account deletion: hide, anonymize, keep only lawfully required records.

A user row is never hard-deleted because payment orders reference it and
financial records must be retained. Personal data is anonymized in the active
system immediately; profiles are hidden and pending notifications cancelled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.contact import REQUEST_PENDING, ContactRequest
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupMedia
from raundo.models.system import OutboxEvent
from raundo.models.user import User, UserProfile

CANCELLABLE_STATES = ("pending", "retrying")


async def delete_account(session: AsyncSession, user: User, *, reason: str = "user_request") -> dict:
    """Anonymize the account and hide everything public. Idempotent."""
    now = datetime.now(timezone.utc)
    report: dict[str, int | str] = {"reason": reason}

    user.deletion_requested_at = user.deletion_requested_at or now
    user.deleted_at = user.deleted_at or now
    user.anonymized_at = now
    user.status = "deleted"
    # Personal data that has no independent retention basis.
    user.username = None
    user.first_name = None
    user.last_name = None

    profile = await session.get(UserProfile, user.id)
    if profile is not None:
        profile.display_name = None
        profile.bio = None
        profile.contact_username = None
        profile.contact_mode = "relay"
        profile.show_contact_after_accept = False

    startups = (
        await session.scalars(select(Startup).where(Startup.owner_user_id == user.id))
    ).all()
    startup_ids = [item.id for item in startups]
    for startup in startups:
        startup.deleted_at = startup.deleted_at or now
        startup.review_state = "deleted"
    if startup_ids:
        await session.execute(delete(StartupMedia).where(StartupMedia.startup_id.in_(startup_ids)))
    report["startups"] = len(startup_ids)

    investors = (
        await session.scalars(select(InvestorProfile).where(InvestorProfile.user_id == user.id))
    ).all()
    for investor in investors:
        investor.deleted_at = investor.deleted_at or now
    report["investor_profiles"] = len(investors)

    orgs = (
        await session.scalars(
            select(FundingOrganization).where(FundingOrganization.created_by_user_id == user.id)
        )
    ).all()
    for org in orgs:
        org.review_status = "deleted"
        org.open_for_requests = False
    report["organizations"] = len(orgs)

    memberships = await session.execute(
        delete(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
    )
    report["memberships"] = memberships.rowcount or 0

    await session.execute(
        update(ContactRequest)
        .where(
            ContactRequest.status == REQUEST_PENDING,
            (ContactRequest.requester_user_id == user.id)
            | (ContactRequest.owner_user_id == user.id),
        )
        .values(status="canceled")
    )

    canceled = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.recipient_user_id == user.id,
            OutboxEvent.state.in_(CANCELLABLE_STATES),
        )
        .values(state="canceled")
    )
    report["outbox_canceled"] = canceled.rowcount or 0

    # Financial records (payment_orders, transactions) and the consent history
    # stay: they rest on a separate legal basis (accounting, dispute resolution).
    await session.flush()
    return report
