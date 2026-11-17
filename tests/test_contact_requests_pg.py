"""PostgreSQL integration for contact requests (mutual consent, anti-spam)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.contact import ContactRequest
from raundo.models.feed import StartupReaction
from raundo.models.startup import Startup
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services import contact_requests as contacts
from raundo.services.accounts import AccountService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 992_900


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
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(old)))
            ).scalars().all()
            if startups:
                await session.execute(
                    delete(ContactRequest).where(ContactRequest.startup_id.in_(startups))
                )
                await session.execute(
                    delete(StartupReaction).where(StartupReaction.startup_id.in_(startups))
                )
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.recipient_user_id.in_(old))
            )
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _user(factory, offset: int, *, username: str | None = None):  # noqa: ANN001, ANN202
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + offset,
            username=username or f"contact_user_{offset}",
            first_name="Contact",
            last_name=str(offset),
            language_code="ru",
        )
        await session.commit()
        return user.id


async def _startup(
    factory,
    owner_id: uuid.UUID,
    name: str,
    *,
    reveal: bool = False,
    username: str | None = None,
):  # noqa: ANN001
    async with factory() as session:
        startup = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner_id,
            slug=f"contact-{uuid.uuid4().hex[:8]}",
            name=name,
            one_liner="Проект для проверки запросов",
            review_state="approved",
            current_revision=1,
            published_at=datetime.now(timezone.utc),
            contact_policy="username" if reveal else "relay",
            contact_username=username,
            show_contact_after_accept=reveal,
        )
        session.add(startup)
        await session.commit()
        return startup.id


async def _outbox_kinds(factory, request_id: uuid.UUID) -> list[str]:
    async with factory() as session:
        rows = await session.execute(
            select(OutboxEvent.kind).where(OutboxEvent.resource_id == str(request_id))
        )
        return list(rows.scalars())


async def test_full_accept_flow_with_mutual_consent(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 1, username="founder_reveal")
    viewer = await _user(factory, 2, username="investor_reveal")
    startup_id = await _startup(
        factory, owner, "Contact Reveal", reveal=True, username="founder_reveal"
    )
    async with factory() as session:
        # Requester consents to reveal their username.
        from raundo.models.user import UserProfile

        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == viewer)
        )
        profile.contact_mode = "username"
        profile.contact_username = "investor_reveal"
        await session.commit()

        request = await contacts.create_request(
            session, requester_id=viewer, startup_id=startup_id, message="Хочу обсудить пилот"
        )
        await session.commit()
        request_id = request.id
        assert request.status == "pending"
        assert request.owner_user_id == owner

        kinds = await _outbox_kinds(factory, request_id)
        assert "contact.requested" in kinds

        decided = await contacts.decide(
            session, owner_id=owner, request_id=request_id, accept=True
        )
        await session.commit()
        assert decided.status == "accepted"

        events = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.resource_id == str(request_id))
            )
        ).scalars().all()
        texts = " ".join((e.safe_payload or {}).get("text", "") for e in events)
        # Mutual consent: founder's and investor's usernames are revealed.
        assert "@founder_reveal" in texts
        assert "@investor_reveal" in texts


async def test_accept_without_reveal_consent_hides_contacts(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 10)
    viewer = await _user(factory, 11)
    startup_id = await _startup(factory, owner, "Contact Private")
    async with factory() as session:
        request = await contacts.create_request(
            session, requester_id=viewer, startup_id=startup_id, message="Давайте знакомиться"
        )
        await session.commit()
        await contacts.decide(session, owner_id=owner, request_id=request.id, accept=True)
        await session.commit()
        events = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.resource_id == str(request.id))
            )
        ).scalars().all()
        texts = " ".join((e.safe_payload or {}).get("text", "") for e in events)
    assert "не раскрыт" in texts.lower() or "через RAUNDO" in texts


async def test_duplicate_decline_repeat_and_self(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 20)
    viewer = await _user(factory, 21)
    startup_id = await _startup(factory, owner, "Contact Dup")
    async with factory() as session:
        with pytest.raises(contacts.ContactError):
            await contacts.create_request(
                session, requester_id=owner, startup_id=startup_id, message="self"
            )
        request = await contacts.create_request(
            session, requester_id=viewer, startup_id=startup_id, message="hello there"
        )
        await session.commit()
        with pytest.raises(contacts.ContactError):
            await contacts.create_request(
                session, requester_id=viewer, startup_id=startup_id, message="again"
            )
        await contacts.decide(session, owner_id=owner, request_id=request.id, accept=False)
        await session.commit()
        with pytest.raises(contacts.ContactError):
            await contacts.create_request(
                session, requester_id=viewer, startup_id=startup_id, message="third time"
            )


async def test_daily_request_limit(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 30)
    viewer = await _user(factory, 31)
    startup_ids = [await _startup(factory, owner, f"Limit {i}") for i in range(6)]
    async with factory() as session:
        for i, startup_id in enumerate(startup_ids[:5]):
            await contacts.create_request(
                session, requester_id=viewer, startup_id=startup_id, message=f"msg {i}"
            )
            await session.commit()
        with pytest.raises(contacts.ContactError) as exc:
            await contacts.create_request(
                session,
                requester_id=viewer,
                startup_id=startup_ids[5],
                message="sixth request",
            )
        assert exc.value.code == "rate_limited"


async def test_owner_lists_only_own_requests(factory) -> None:  # noqa: ANN001
    owner = await _user(factory, 40)
    other_owner = await _user(factory, 41)
    viewer = await _user(factory, 42)
    startup_id = await _startup(factory, owner, "Contact List")
    other_startup = await _startup(factory, other_owner, "Contact Other")
    async with factory() as session:
        request = await contacts.create_request(
            session, requester_id=viewer, startup_id=startup_id, message="to owner"
        )
        await contacts.create_request(
            session, requester_id=viewer, startup_id=other_startup, message="to other"
        )
        await session.commit()
        mine = await contacts.requests_for_owner(session, owner)
        theirs = await contacts.requests_for_owner(session, other_owner)
    assert [row[0].id for row in mine] == [request.id]
    assert len(theirs) == 1
