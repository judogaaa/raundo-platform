"""Public checkout API + YooKassa webhook on PostgreSQL (suite DB only)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from raundo.config import Settings, get_settings
from raundo.db.session import get_session_factory
from raundo.models.billing import PremiumSubscription
from raundo.models.payments import PaymentOrder
from raundo.models.startup import Startup
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services import payments as pay
from raundo.services.accounts import AccountService
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 994_500


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

SETTINGS = Settings(
    _env_file=None,
    PAYMENTS_MODE="provider_test",
    PAYMENTS_CARD_ENABLED="true",
    YOOKASSA_SHOP_ID="test-shop",
    YOOKASSA_SECRET_KEY="test-secret",
    CHECKOUT_BASE_URL="http://checkout.test",
)


@dataclass
class FakePayment:
    id: str
    status: str
    paid: bool
    amount_minor: int
    currency: str
    confirmation_url: str | None
    metadata: dict
    test: bool = True


@dataclass
class FakeRefund:
    id: str
    status: str
    payment_id: str
    amount_minor: int
    currency: str


class FakeYooKassa:
    def __init__(self, payment: FakePayment) -> None:
        self.payment = payment
        self.refund: FakeRefund | None = None
        self.created: list[dict] = []

    async def create_payment(self, **kwargs):  # noqa: ANN003, ANN201
        self.created.append(kwargs)
        return self.payment

    async def get_payment(self, payment_id: str):  # noqa: ANN201
        assert payment_id == self.payment.id
        return self.payment

    async def get_refund(self, refund_id: str):  # noqa: ANN201
        assert self.refund is not None
        assert refund_id == self.refund.id
        return self.refund


def _refund(payment: FakePayment, *, amount_minor: int | None = None) -> FakeRefund:
    return FakeRefund(
        id="ref_1",
        status="succeeded",
        payment_id=payment.id,
        amount_minor=payment.amount_minor if amount_minor is None else amount_minor,
        currency=payment.currency,
    )


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        users = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 50)
            )
        ).scalars().all()
        if users:
            await session.execute(
                delete(OutboxEvent).where(
                    OutboxEvent.resource_type == "payment_order"
                )
            )
            startups = (
                await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(users)))
            ).scalars().all()
            if startups:
                await session.execute(delete(Startup).where(Startup.id.in_(startups)))
            await session.execute(delete(User).where(User.id.in_(users)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


def _client() -> httpx.AsyncClient:
    from raundo.admin_api.app import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://checkout.test"
    )


async def _order(factory, *, telegram_id: int = TG_BASE + 1):  # noqa: ANN001, ANN202
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=telegram_id,
            username=f"payer{telegram_id}",
            first_name="Payer",
            last_name="Test",
            language_code="ru",
        )
        order, token = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="7d",
            provider=pay.PROVIDER_YOOKASSA,
            settings=SETTINGS,
        )
        await session.commit()
        return order.id, token, user.id


@pytest.mark.asyncio
async def test_checkout_is_server_priced_and_requires_consent(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments

    order_id, token, _user_id = await _order(factory)
    app_settings = SETTINGS
    from raundo.admin_api.app import app

    app.dependency_overrides[get_settings] = lambda: app_settings
    try:
        async with _client() as client:
            res = await client.get(f"/api/public/checkout/{token}?amount=1&plan=startup_pro")
            assert res.status_code == 200
            body = res.json()
            assert body["amount"]["minor"] == 9900
            assert body["plan"]["code"] == "community_plus"
            assert "amount=1" not in res.text
            assert res.headers["cache-control"].startswith("no-store")

            no_consent = await client.post(f"/api/public/checkout/{token}/pay", json={"consent": False})
            assert no_consent.status_code == 400

            fake = FakeYooKassa(
                FakePayment(
                    id="pay_1",
                    status="pending",
                    paid=False,
                    amount_minor=9900,
                    currency="RUB",
                    confirmation_url="https://pay.test/1",
                    metadata={"order_id": str(order_id)},
                )
            )
            original = public_payments.yookassa_client
            public_payments.yookassa_client = lambda settings: fake
            try:
                paid = await client.post(
                    f"/api/public/checkout/{token}/pay", json={"consent": True}
                )
            finally:
                public_payments.yookassa_client = original
            assert paid.status_code == 200, paid.text
            assert paid.json()["confirmation_url"] == "https://pay.test/1"
            assert fake.created[0]["amount_minor"] == 9900
            assert fake.created[0]["idempotence_key"] == f"order:{order_id}"

            # Unknown token is not enumerable.
            assert (await client.get("/api/public/checkout/not-a-token")).status_code == 404
    finally:
        app.dependency_overrides.clear()

    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None
        assert order.status == "pending"
        assert order.consent_accepted_at is not None
        assert order.consent_version == SETTINGS.payment_consent_version


@pytest.mark.asyncio
async def test_webhook_verifies_provider_and_is_idempotent(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    order_id, token, user_id = await _order(factory, telegram_id=TG_BASE + 2)
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    fake = FakeYooKassa(
        FakePayment(
            id="pay_ok",
            status="pending",
            paid=False,
            amount_minor=9900,
            currency="RUB",
            confirmation_url="https://pay.test/ok",
            metadata={"order_id": str(order_id)},
        )
    )
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            # A real checkout first: only then does the payment belong to the order.
            paid = await client.post(
                f"/api/public/checkout/{token}/pay", json={"consent": True}
            )
            assert paid.status_code == 200, paid.text
            fake.payment.status = "succeeded"
            fake.payment.paid = True
            payload = {"event": "payment.succeeded", "object": {"id": "pay_ok"}}
            first = await client.post("/api/public/payments/yookassa/webhook", json=payload)
            assert first.status_code == 200, first.text
            second = await client.post("/api/public/payments/yookassa/webhook", json=payload)
            assert second.status_code == 200, second.text

            status = await client.get(f"/api/public/checkout/{token}/status")
            assert status.json()["status"] == "paid"
            assert status.json()["paid_until"]
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()

    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status == "paid"
        subs = (
            await session.execute(
                select(PremiumSubscription).where(PremiumSubscription.user_id == user_id)
            )
        ).scalars().all()
        assert len(subs) == 1
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.resource_type == "payment_order",
                    OutboxEvent.resource_id == str(order_id),
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].kind == "premium.purchased"


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_amount(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    order_id, _token, _user_id = await _order(factory, telegram_id=TG_BASE + 3)
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    fake = FakeYooKassa(
        FakePayment(
            id="pay_bad",
            status="succeeded",
            paid=True,
            amount_minor=1,
            currency="RUB",
            confirmation_url=None,
            metadata={"order_id": str(order_id)},
        )
    )
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            res = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "payment.succeeded", "object": {"id": "pay_bad"}},
            )
            assert res.status_code == 400
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status != "paid"


@pytest.mark.asyncio
async def test_demo_payment_never_activates(factory) -> None:  # noqa: ANN001
    from raundo.admin_api.app import app

    demo_settings = Settings(
        _env_file=None,
        PAYMENTS_MODE="demo",
        PAYMENTS_CARD_ENABLED="true",
        CHECKOUT_BASE_URL="http://checkout.test",
    )
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 4,
            username="demo_payer",
            first_name="Demo",
            last_name="Payer",
            language_code="ru",
        )
        order, token = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="3d",
            provider=pay.PROVIDER_DEMO,
            settings=demo_settings,
        )
        await session.commit()
        order_id = order.id
    app.dependency_overrides[get_settings] = lambda: demo_settings
    try:
        async with _client() as client:
            res = await client.post(f"/api/public/checkout/{token}/pay", json={"consent": True})
            assert res.status_code == 200
            assert res.json()["status"] == "demo"
            assert res.json()["paid"] is False
    finally:
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status != "paid"
        subs = (
            await session.execute(
                select(PremiumSubscription).where(PremiumSubscription.user_id == user.id)
            )
        ).scalars().all()
        assert subs == []


@pytest.mark.asyncio
async def test_expired_token_returns_410(factory) -> None:  # noqa: ANN001
    from raundo.admin_api.app import app

    order_id, token, _ = await _order(factory, telegram_id=TG_BASE + 5)
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        order.checkout_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    try:
        async with _client() as client:
            res = await client.get(f"/api/public/checkout/{token}")
            assert res.status_code == 410
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_webhook_rejects_payment_without_transaction(factory) -> None:  # noqa: ANN001
    """A succeeded payment that was not created by our checkout cannot activate."""
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    order_id, _token, _user_id = await _order(factory, telegram_id=TG_BASE + 6)
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    fake = FakeYooKassa(
        FakePayment(
            id="pay_foreign",
            status="succeeded",
            paid=True,
            amount_minor=9900,
            currency="RUB",
            confirmation_url=None,
            metadata={"order_id": str(order_id)},
        )
    )
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            res = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "payment.succeeded", "object": {"id": "pay_foreign"}},
            )
            assert res.status_code == 400
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status != "paid"


async def _pending_then_paid(factory, telegram_id: int):  # noqa: ANN001, ANN202
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    order_id, token, _user_id = await _order(factory, telegram_id=telegram_id)
    fake = FakeYooKassa(
        FakePayment(
            id=f"pay_{telegram_id}",
            status="pending",
            paid=False,
            amount_minor=9900,
            currency="RUB",
            confirmation_url="https://pay.test/1",
            metadata={"order_id": str(order_id)},
        )
    )
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    try:
        async with _client() as client:
            paid = await client.post(
                f"/api/public/checkout/{token}/pay", json={"consent": True}
            )
            assert paid.status_code == 200, paid.text
            fake.payment.status = "succeeded"
            fake.payment.paid = True
            hook = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "payment.succeeded", "object": {"id": fake.payment.id}},
            )
            assert hook.status_code == 200, hook.text
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    return fake, order_id


@pytest.mark.asyncio
async def test_refund_webhook_refunds_once_and_ends_entitlement(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    fake, order_id = await _pending_then_paid(factory, TG_BASE + 7)
    fake.refund = _refund(fake.payment)
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            payload = {"event": "refund.succeeded", "object": {"id": "ref_1"}}
            first = await client.post("/api/public/payments/yookassa/webhook", json=payload)
            assert first.status_code == 200, first.text
            second = await client.post("/api/public/payments/yookassa/webhook", json=payload)
            assert second.status_code == 200, second.text
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status == "refunded"
        sub = await session.get(PremiumSubscription, order.premium_subscription_id)
        assert sub is not None and sub.status == "cancelled"
        from raundo.models.tax import TaxReceipt

        receipt = await session.scalar(
            select(TaxReceipt).where(TaxReceipt.order_id == order_id)
        )
        assert receipt is not None and receipt.status == "refunded"
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.kind == "payment.refunded",
                    OutboxEvent.resource_id == str(order_id),
                )
            )
        ).scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_partial_refund_keeps_entitlement(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    fake, order_id = await _pending_then_paid(factory, TG_BASE + 8)
    fake.refund = _refund(fake.payment, amount_minor=100)
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            res = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "refund.succeeded", "object": {"id": "ref_1"}},
            )
            assert res.status_code == 200, res.text
            assert res.json().get("partial") is True
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status == "paid"
        sub = await session.get(PremiumSubscription, order.premium_subscription_id)
        assert sub is not None and sub.status == "active"


@pytest.mark.asyncio
async def test_canceled_payment_closes_pending_order(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    order_id, token, _user_id = await _order(factory, telegram_id=TG_BASE + 9)
    fake = FakeYooKassa(
        FakePayment(
            id="pay_cancel",
            status="pending",
            paid=False,
            amount_minor=9900,
            currency="RUB",
            confirmation_url="https://pay.test/1",
            metadata={"order_id": str(order_id)},
        )
    )
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    try:
        async with _client() as client:
            paid = await client.post(
                f"/api/public/checkout/{token}/pay", json={"consent": True}
            )
            assert paid.status_code == 200, paid.text
            fake.payment.status = "canceled"
            res = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "payment.canceled", "object": {"id": "pay_cancel"}},
            )
            assert res.status_code == 200, res.text
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status == "canceled"


@pytest.mark.asyncio
async def test_late_canceled_event_does_not_downgrade_paid_order(factory) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    fake, order_id = await _pending_then_paid(factory, TG_BASE + 10)
    fake.payment.status = "canceled"
    fake.payment.paid = False
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    original = public_payments.yookassa_client
    public_payments.yookassa_client = lambda settings: fake
    try:
        async with _client() as client:
            res = await client.post(
                "/api/public/payments/yookassa/webhook",
                json={"event": "payment.canceled", "object": {"id": fake.payment.id}},
            )
            assert res.status_code == 200, res.text
    finally:
        public_payments.yookassa_client = original
        app.dependency_overrides.clear()
    async with factory() as session:
        order = await session.get(PaymentOrder, order_id)
        assert order is not None and order.status == "paid"
