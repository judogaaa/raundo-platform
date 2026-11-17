"""Payment orders: server snapshot, consent, provider confirmation, activation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from raundo.config import Settings
from raundo.models.payments import ORDER_PAID, ORDER_REFUNDED
from raundo.models.startup import Startup
from raundo.payments_catalog import rub_offer
from raundo.services import payments as pay
from raundo.services.accounts import AccountService
from raundo.services.premium import PremiumService


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        PAYMENTS_MODE="provider_test",
        PAYMENTS_CARD_ENABLED="true",
        YOOKASSA_SHOP_ID="test-shop",
        YOOKASSA_SECRET_KEY="test-secret",
        CHECKOUT_BASE_URL="https://checkout.example.test",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _user(session, telegram_id: int = 79001):  # noqa: ANN001
    return await AccountService(session).bind_telegram_user(
        telegram_id=telegram_id,
        username=f"pay{telegram_id}",
        first_name="Pay",
        last_name="User",
        language_code="ru",
    )


@pytest.mark.asyncio
async def test_order_snapshots_server_price_and_hides_token(session) -> None:
    user = await _user(session)
    settings = _settings()
    order, token = await pay.create_order(
        session,
        user=user,
        plan_code="community_plus",
        duration_code="7d",
        provider=pay.PROVIDER_YOOKASSA,
        settings=settings,
    )
    offer = rub_offer("community_plus", "7d")
    assert offer is not None
    assert order.amount_minor == offer.amount_minor
    assert order.currency == "RUB"
    assert order.plan_code == "community_plus"
    assert order.duration_code == "7d"
    assert order.status == "created"
    # The raw token is not stored anywhere; only its hash identifies the order.
    assert token not in order.checkout_token_hash
    assert order.checkout_token_hash == pay.hash_token(token)
    stored = await pay.get_order_by_token(session, token)
    assert stored is not None and stored.id == order.id
    payload = pay.public_order_payload(order, settings)
    assert payload["amount"]["minor"] == offer.amount_minor
    assert "user" not in payload
    assert "email" not in str(payload).lower()
    assert payload["consent"]["required"] is True


@pytest.mark.asyncio
async def test_checkout_url_uses_configured_base() -> None:
    settings = _settings()
    assert pay.checkout_url(settings, "abc") == "https://checkout.example.test/checkout/abc"


@pytest.mark.asyncio
async def test_activation_requires_matching_amount_and_is_idempotent(session) -> None:
    user = await _user(session, 79002)
    settings = _settings()
    order, _token = await pay.create_order(
        session,
        user=user,
        plan_code="investor_pro",
        duration_code="30d",
        provider=pay.PROVIDER_YOOKASSA,
        settings=settings,
    )
    with pytest.raises(pay.PaymentError) as wrong:
        await pay.activate_order(
            session,
            order_id=order.id,
            provider=pay.PROVIDER_YOOKASSA,
            provider_payment_id="pay_wrong",
            charge_id=None,
            amount_minor=1,
            currency="RUB",
        )
    assert wrong.value.code == "amount_mismatch"

    confirmed, sub, created = await pay.activate_order(
        session,
        order_id=order.id,
        provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_1",
        charge_id=None,
        amount_minor=order.amount_minor,
        currency="RUB",
    )
    assert created is True
    assert confirmed.status == ORDER_PAID
    assert sub is not None and sub.plan_code == "investor_pro"
    assert confirmed.premium_subscription_id == sub.id

    again, sub2, created2 = await pay.activate_order(
        session,
        order_id=order.id,
        provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_1",
        charge_id=None,
        amount_minor=order.amount_minor,
        currency="RUB",
    )
    assert created2 is False
    assert sub2 is not None and sub2.id == sub.id
    subs = await PremiumService(session).my_subscriptions(user.id)
    assert len(subs) == 1


@pytest.mark.asyncio
async def test_second_paid_order_extends_active_subscription(session) -> None:
    user = await _user(session, 79003)
    settings = _settings()
    first, _ = await pay.create_order(
        session, user=user, plan_code="investor_pro", duration_code="7d",
        provider=pay.PROVIDER_YOOKASSA, settings=settings,
    )
    _o, sub, _ = await pay.activate_order(
        session, order_id=first.id, provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_a", charge_id=None,
        amount_minor=first.amount_minor, currency="RUB",
    )
    assert sub is not None
    before = sub.expires_at
    second, _ = await pay.create_order(
        session, user=user, plan_code="investor_pro", duration_code="7d",
        provider=pay.PROVIDER_YOOKASSA, settings=settings,
    )
    _o2, extended, created = await pay.activate_order(
        session, order_id=second.id, provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_b", charge_id=None,
        amount_minor=second.amount_minor, currency="RUB",
    )
    assert created is False
    assert extended is not None and extended.id == sub.id
    assert extended.expires_at > before
    assert extended.expires_at - before == timedelta(hours=168)


@pytest.mark.asyncio
async def test_startup_pro_requires_owned_startup(session) -> None:
    user = await _user(session, 79004)
    other = await _user(session, 79005)
    settings = _settings()
    foreign = Startup(
        id=uuid.uuid4(),
        owner_user_id=other.id,
        slug=f"foreign-{uuid.uuid4().hex[:6]}",
        name="Foreign",
        review_state="approved",
        current_revision=1,
        published_at=datetime.now(timezone.utc),
    )
    session.add(foreign)
    await session.flush()
    with pytest.raises(pay.PaymentError):
        await pay.create_order(
            session,
            user=user,
            plan_code="startup_pro",
            duration_code="30d",
            provider=pay.PROVIDER_YOOKASSA,
            settings=settings,
            startup_id=foreign.id,
        )
    own = Startup(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        slug=f"own-{uuid.uuid4().hex[:6]}",
        name="Own",
        review_state="approved",
        current_revision=1,
        published_at=datetime.now(timezone.utc),
    )
    session.add(own)
    await session.flush()
    order, _ = await pay.create_order(
        session,
        user=user,
        plan_code="startup_pro",
        duration_code="30d",
        provider=pay.PROVIDER_YOOKASSA,
        settings=settings,
        startup_id=own.id,
    )
    assert order.target_startup_id == own.id
    _o, sub, _ = await pay.activate_order(
        session, order_id=order.id, provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_startup", charge_id=None,
        amount_minor=order.amount_minor, currency="RUB",
    )
    assert sub is not None and sub.startup_id == own.id


@pytest.mark.asyncio
async def test_expired_token_and_refund(session) -> None:
    user = await _user(session, 79006)
    settings = _settings()
    order, _ = await pay.create_order(
        session, user=user, plan_code="community_plus", duration_code="3d",
        provider=pay.PROVIDER_YOOKASSA, settings=settings,
    )
    order.checkout_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert pay.token_expired(order) is True
    _o, _sub, _ = await pay.activate_order(
        session, order_id=order.id, provider=pay.PROVIDER_YOOKASSA,
        provider_payment_id="pay_r", charge_id=None,
        amount_minor=order.amount_minor, currency="RUB",
    )
    refunded = await pay.refund_order(
        session, order=order, refund_status="succeeded", reason="request"
    )
    assert refunded.status == ORDER_REFUNDED
    # History is preserved: the subscription row is not deleted on refund.
    subs = await PremiumService(session).my_subscriptions(user.id)
    assert len(subs) == 1


@pytest.mark.asyncio
async def test_stars_disabled_without_approval(session) -> None:
    user = await _user(session, 79007)
    settings = _settings(PAYMENTS_STARS_ENABLED="true", PAYMENTS_MODE="live")
    with pytest.raises(pay.PaymentError) as exc:
        await pay.create_order(
            session, user=user, plan_code="community_plus", duration_code="7d",
            provider=pay.PROVIDER_STARS, settings=settings,
        )
    assert exc.value.code == "stars_disabled"
