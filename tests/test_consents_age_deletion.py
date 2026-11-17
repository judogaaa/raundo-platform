"""Separate consents, age brackets and account deletion (service level)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from raundo.models.contact import ContactRequest
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.payments import PaymentOrder
from raundo.models.startup import Startup, StartupMedia
from raundo.models.system import OutboxEvent
from raundo.models.user import User, UserConsent
from raundo.services.account_deletion import delete_account
from raundo.services.accounts import AccountService
from raundo.services.age import AGE_ADULT, AGE_MINOR, confirm_age, is_adult
from raundo.services.consents import (
    CONSENT_DATA,
    CONSENT_MARKETING,
    CONSENT_PROFILE_PUBLICATION,
    ConsentError,
    ConsentService,
)


async def _user(session, telegram_id: int = 7001) -> User:  # noqa: ANN001
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username="tester",
        first_name="Test",
        last_name="User",
        language_code="ru",
    )


@pytest.mark.asyncio
async def test_required_consents_are_three_separate_records(session) -> None:
    user = await _user(session)
    service = ConsentService(session)
    assert not await service.has_required_consents(user.id)
    assert set(await service.missing_required(user.id)) == {"terms", "privacy", "data_processing"}

    await service.accept_required(user.id, source="test")
    assert await service.has_required_consents(user.id)

    rows = (await session.execute(select(UserConsent))).scalars().all()
    by_kind = {row.kind: row for row in rows}
    assert set(by_kind) == {"terms", "privacy", "data_processing"}
    assert by_kind[CONSENT_DATA].purpose == "platform_operation"
    assert by_kind[CONSENT_DATA].source == "test"
    assert by_kind[CONSENT_DATA].granted_at is not None
    assert all(row.revoked_at is None for row in rows)


@pytest.mark.asyncio
async def test_optional_consents_are_off_until_explicitly_granted(session) -> None:
    user = await _user(session)
    service = ConsentService(session)
    await service.accept_required(user.id, source="test")
    active = await service.active(user.id)
    assert CONSENT_MARKETING not in active
    assert CONSENT_PROFILE_PUBLICATION not in active

    granted = await service.grant(
        user.id,
        CONSENT_PROFILE_PUBLICATION,
        fields=["display_name", "bio"],
        source="settings",
    )
    assert granted.fields == ["display_name", "bio"]
    assert CONSENT_PROFILE_PUBLICATION in await service.active(user.id)

    revoked = await service.revoke(user.id, CONSENT_PROFILE_PUBLICATION, reason="user_request")
    assert revoked.revoked_at is not None
    assert revoked.revoke_reason == "user_request"
    assert CONSENT_PROFILE_PUBLICATION not in await service.active(user.id)


@pytest.mark.asyncio
async def test_required_consent_cannot_be_revoked(session) -> None:
    user = await _user(session)
    service = ConsentService(session)
    await service.accept_required(user.id)
    with pytest.raises(ConsentError):
        await service.revoke(user.id, "terms")
    with pytest.raises(ConsentError):
        await service.revoke(user.id, CONSENT_MARKETING)  # nothing granted


@pytest.mark.asyncio
async def test_revoked_consent_stays_in_history(session) -> None:
    user = await _user(session)
    service = ConsentService(session)
    await service.grant(user.id, CONSENT_MARKETING, source="settings")
    await service.revoke(user.id, CONSENT_MARKETING)
    history = await service.history(user.id)
    assert len(history) == 1
    assert history[0].kind == CONSENT_MARKETING
    assert history[0].revoked_at is not None


def test_age_brackets_are_self_declared_only() -> None:
    user = User(telegram_id=1)
    assert user.age_bracket is None
    assert not is_adult(user)
    confirm_age(user, AGE_MINOR)
    assert user.age_bracket == AGE_MINOR
    assert user.age_confirmed_at is not None
    assert not is_adult(user)
    confirm_age(user, AGE_ADULT)
    assert is_adult(user)
    with pytest.raises(ValueError):
        confirm_age(user, "21_plus")


@pytest.mark.asyncio
async def test_deletion_anonymizes_and_keeps_financial_records(session) -> None:
    user = await _user(session, telegram_id=7002)
    startup = Startup(owner_user_id=user.id, slug="hidden", name="Hidden", review_state="approved")
    session.add(startup)
    await session.flush()
    session.add(
        StartupMedia(
            startup_id=startup.id,
            telegram_file_id="demo:x.png",
            file_unique_id="uniq-x",
            position=1,
        )
    )
    investor = InvestorProfile(user_id=user.id, review_status="approved")
    session.add(investor)
    org = FundingOrganization(
        name="Org", normalized_name="org", participant_type="fund", created_by_user_id=user.id
    )
    session.add(org)
    await session.flush()
    session.add(
        OrganizationMembership(
            organization_id=org.id, user_id=user.id, role="founder", status="approved"
        )
    )
    session.add(
        OutboxEvent(
            kind="test",
            recipient_user_id=user.id,
            dedupe_key=f"del:{uuid.uuid4()}",
            safe_payload={},
            state="pending",
        )
    )
    session.add(
        PaymentOrder(
            order_number="PAY-900001",
            user_id=user.id,
            plan_code="community_plus",
            duration_code="7d",
            amount_minor=9900,
            currency="RUB",
            payment_provider="demo",
            status="created",
            checkout_token_hash="deadbeef",
            checkout_token_expires_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    report = await delete_account(session, user)
    await session.commit()

    assert report["startups"] == 1
    assert user.deleted_at is not None
    assert user.anonymized_at is not None
    assert user.username is None and user.first_name is None
    assert user.status == "deleted"

    await session.refresh(startup)
    assert startup.deleted_at is not None
    assert startup.review_state == "deleted"
    assert (
        await session.scalar(select(StartupMedia).where(StartupMedia.startup_id == startup.id))
        is None
    )

    await session.refresh(investor)
    assert investor.deleted_at is not None
    await session.refresh(org)
    assert org.review_status == "deleted"
    assert (
        await session.scalar(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        )
        is None
    )

    event = (await session.execute(select(OutboxEvent))).scalar_one()
    assert event.state == "canceled"

    # Financial record must survive and stay linked.
    order = (await session.execute(select(PaymentOrder))).scalar_one()
    assert order.user_id == user.id
    assert order.amount_minor == 9900


@pytest.mark.asyncio
async def test_deletion_cancels_pending_contact_requests(session) -> None:
    user = await _user(session, telegram_id=7003)
    other = await _user(session, telegram_id=7004)
    startup = Startup(owner_user_id=other.id, slug="other", name="Other", review_state="approved")
    session.add(startup)
    await session.flush()
    session.add(
        ContactRequest(
            startup_id=startup.id,
            requester_user_id=user.id,
            owner_user_id=other.id,
            status="pending",
            message="hi",
        )
    )
    await session.commit()

    await delete_account(session, user)
    await session.commit()

    request = (await session.execute(select(ContactRequest))).scalar_one()
    assert request.status == "canceled"

@pytest.mark.asyncio
async def test_data_processing_revocation_stops_processing(session) -> None:
    user = await _user(session, telegram_id=7010)
    service = ConsentService(session)
    await service.accept_required(user.id, source="test")
    await service.grant(user.id, CONSENT_MARKETING, source="settings")
    assert await service.has_required_consents(user.id)

    result = await service.revoke_data_processing(user.id, reason="user_request")
    await session.commit()

    assert CONSENT_DATA in result["revoked"]
    assert result["edition"] == service.policy_version
    assert not await service.has_required_consents(user.id)
    active = await service.active(user.id)
    assert CONSENT_DATA not in active
    assert CONSENT_MARKETING not in active

    rows = await session.execute(
        select(UserConsent).where(UserConsent.kind == CONSENT_DATA)
    )
    data_row = rows.scalar_one()
    assert data_row.revoked_at is not None
    assert data_row.revoke_reason == "user_request"
    assert data_row.policy_version == service.policy_version

    # History keeps the record instead of deleting it.
    history = await service.history(user.id)
    assert any(row.kind == CONSENT_DATA and row.revoked_at is not None for row in history)


@pytest.mark.asyncio
async def test_revocation_does_not_touch_another_user(session) -> None:
    first = await _user(session, telegram_id=7011)
    second = await _user(session, telegram_id=7012)
    service = ConsentService(session)
    await service.accept_required(first.id, source="test")
    await service.accept_required(second.id, source="test")

    await service.revoke_data_processing(first.id)
    await session.commit()

    assert not await service.has_required_consents(first.id)
    assert await service.has_required_consents(second.id)
    second_active = await service.active(second.id)
    assert CONSENT_DATA in second_active


@pytest.mark.asyncio
async def test_revoking_terms_is_forbidden(session) -> None:
    user = await _user(session, telegram_id=7013)
    service = ConsentService(session)
    await service.accept_required(user.id)
    with pytest.raises(ConsentError):
        await service.revoke(user.id, "terms")
    with pytest.raises(ConsentError):
        await service.revoke(user.id, "privacy")