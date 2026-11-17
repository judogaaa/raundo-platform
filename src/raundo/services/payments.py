"""Premium payment orders: secure checkout, provider confirmation, activation.

Security invariants:
- the browser sees only a random token; the DB stores its SHA-256;
- plan, period and amount come from the server snapshot, never from the client;
- Premium is granted only from a provider-confirmed payment with matching amount,
  currency and order, inside one transaction with the order row locked;
- repeated confirmations are idempotent and never create a second grant.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import Settings
from raundo.legal_edition import legal_edition
from raundo.models.admin_panel import AdminAuditLog
from raundo.models.billing import PremiumSubscription
from raundo.models.organization import FundingOrganization
from raundo.models.payments import (
    ORDER_CANCELED,
    ORDER_CREATED,
    ORDER_EXPIRED,
    ORDER_PAID,
    ORDER_PENDING,
    ORDER_REFUNDED,
    PROVIDER_DEMO,
    PROVIDER_STARS,
    PROVIDER_YOOKASSA,
    PaymentOrder,
    PaymentTransaction,
)
from raundo.models.startup import Startup
from raundo.models.user import User
from raundo.payments_catalog import PaymentOffer, rub_offer
from raundo.premium_catalog import PLAN_TITLES
from raundo.services.notifications import NotificationService
from raundo.services.premium import (
    DURATION_HOURS,
    OWNER_ORGANIZATION,
    OWNER_STARTUP,
    PLAN_OWNER_TYPE,
    SUB_STATUS_ACTIVE,
    SUB_STATUS_CANCELLED,
    SUB_STATUS_EXPIRED,
    PremiumService,
    PremiumServiceError,
    ensure_utc,
)

TOKEN_TTL = timedelta(minutes=60)
ORDER_TTL = timedelta(hours=24)
PROVIDER_STATUSES = frozenset({"pending", "waiting_for_capture", "succeeded", "canceled"})
PAID_STATUSES = frozenset({"succeeded", "paid"})


class PaymentError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        self.message = message
        self.code = code
        super().__init__(message)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _order_number(suffix: int) -> str:
    return f"PAY-{suffix:06d}"


async def _next_order_number(session: AsyncSession) -> str:
    numbers = (await session.execute(select(PaymentOrder.order_number))).scalars()
    highest = 0
    for number in numbers:
        suffix = (number or "").removeprefix("PAY-")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return _order_number(highest + 1)


def is_live(settings: Settings) -> bool:
    return settings.payments_mode == "live"


def provider_available(settings: Settings) -> bool:
    if settings.payments_mode == "demo":
        return settings.payments_card_enabled
    # The key comes from the environment (dev) or a mounted Docker secret.
    return settings.yookassa_configured()


def stars_available(settings: Settings) -> bool:
    if not settings.payments_stars_enabled:
        return False
    # Real Stars charging requires approved prices and live readiness.
    if settings.payments_mode == "live":
        return bool(settings.payments_stars_prices_approved and settings.payments_legal_ready)
    return True


def _target_values(
    user: User,
    offer: PaymentOffer,
    startup_id: uuid.UUID | None,
    organization_id: uuid.UUID | None,
    startup: Startup | None,
    organization: FundingOrganization | None,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    owner_type = PLAN_OWNER_TYPE[offer.plan_code]
    if owner_type == OWNER_STARTUP:
        if startup is None or startup.owner_user_id != user.id or startup.deleted_at is not None:
            raise PaymentError("Выберите свой стартап для этого тарифа.", code="target")
        return startup.id, None
    if owner_type == OWNER_ORGANIZATION:
        if organization is None or organization.created_by_user_id != user.id:
            raise PaymentError("Выберите свою организацию для этого тарифа.", code="target")
        return None, organization.id
    if startup_id is not None or organization_id is not None:
        raise PaymentError("Этот тариф оформляется на пользователя.", code="target")
    return None, None


async def create_order(
    session: AsyncSession,
    *,
    user: User,
    plan_code: str,
    duration_code: str,
    provider: str,
    settings: Settings,
    startup_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> tuple[PaymentOrder, str]:
    offer = rub_offer(plan_code, duration_code)
    if offer is None:
        raise PaymentError("Тариф или срок недоступны.", code="unknown_offer")
    if provider not in {PROVIDER_YOOKASSA, PROVIDER_STARS, PROVIDER_DEMO}:
        raise PaymentError("Неизвестный способ оплаты.", code="provider")
    if provider == PROVIDER_STARS:
        if not stars_available(settings) or offer.stars_amount is None:
            raise PaymentError("Оплата Stars пока недоступна.", code="stars_disabled")
    elif provider == PROVIDER_YOOKASSA:
        if not provider_available(settings):
            raise PaymentError("Оплата картой пока недоступна.", code="card_disabled")

    startup = await session.get(Startup, startup_id) if startup_id else None
    organization = (
        await session.get(FundingOrganization, organization_id) if organization_id else None
    )
    target_startup, target_org = _target_values(
        user, offer, startup_id, organization_id, startup, organization
    )

    amount_minor = offer.amount_minor
    currency = offer.currency
    if provider == PROVIDER_STARS:
        # Stars are a separate price in XTR, never a converted RUB amount.
        amount_minor = int(offer.stars_amount or 0)
        currency = "XTR"
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    order = PaymentOrder(
        id=uuid.uuid4(),
        order_number=await _next_order_number(session),
        user_id=user.id,
        plan_code=offer.plan_code,
        plan_version=offer.plan_version,
        duration_code=offer.duration_code,
        amount_minor=amount_minor,
        currency=currency,
        payment_provider=provider,
        status=ORDER_CREATED,
        checkout_token_hash=hash_token(token),
        checkout_token_expires_at=now + TOKEN_TTL,
        expires_at=now + ORDER_TTL,
        target_startup_id=target_startup,
        target_organization_id=target_org,
        is_test=settings.payments_mode != "live",
        offer_edition=legal_edition(),
        terms_edition=legal_edition(),
    )
    session.add(order)
    await session.flush()
    return order, token


def checkout_url(settings: Settings, token: str) -> str:
    base = (settings.checkout_base_url or "").rstrip("/")
    return f"{base}/checkout/{token}"


async def get_order_by_token(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> PaymentOrder | None:
    if not token or len(token) > 200:
        return None
    stmt = select(PaymentOrder).where(PaymentOrder.checkout_token_hash == hash_token(token))
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


def token_expired(order: PaymentOrder, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return order.checkout_token_expires_at <= now


def public_order_payload(order: PaymentOrder, settings: Settings) -> dict:
    offer = rub_offer(order.plan_code, order.duration_code)
    if offer is None:
        raise PaymentError("Тариф больше недоступен.", code="unknown_offer")
    seller = None
    if settings.payment_seller_name:
        seller = {
            "name": settings.payment_seller_name,
            "inn": settings.payment_seller_inn or None,
            "contact": settings.payment_seller_contact or None,
        }
    return {
        "order_number": order.order_number,
        "status": order.status,
        "plan": {
            "code": offer.plan_code,
            "title": offer.title,
            "audience": offer.audience,
            "benefits": list(offer.benefits),
            "period": offer.duration_title,
            "hours": offer.duration_hours,
        },
        "amount": {
            "minor": order.amount_minor,
            "currency": order.currency,
            "label": offer.price_label,
        },
        "mode": settings.payments_mode,
        "is_test": order.is_test,
        "consent": {
            "required": True,
            "version": settings.payment_consent_version,
            "accepted": order.consent_accepted_at is not None,
        },
        "legal": {
            "terms_url": "/legal/terms",
            "privacy_url": "/legal/privacy",
            "seller": seller,
            "refund_note": "Возврат рассматривается поддержкой RAUNDO через /support.",
        },
        "card_available": provider_available(settings),
        "stars_available": stars_available(settings),
        "expires_at": order.checkout_token_expires_at.isoformat(),
        "paid_until": None,
    }


async def accept_consent(order: PaymentOrder, version: str) -> None:
    if order.consent_accepted_at is None:
        order.consent_version = version
        order.consent_accepted_at = datetime.now(timezone.utc)
    elif order.consent_version != version:
        order.consent_version = version
        order.consent_accepted_at = datetime.now(timezone.utc)


async def record_pending_transaction(
    session: AsyncSession,
    *,
    order: PaymentOrder,
    provider: str,
    provider_payment_id: str,
    amount_minor: int,
    currency: str,
    provider_status: str,
    safe_payload: dict | None = None,
) -> PaymentTransaction:
    existing = await session.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.provider == provider,
            PaymentTransaction.provider_payment_id == provider_payment_id,
        )
    )
    if existing is not None:
        return existing
    tx = PaymentTransaction(
        id=uuid.uuid4(),
        order_id=order.id,
        provider=provider,
        provider_payment_id=provider_payment_id,
        amount_minor=amount_minor,
        currency=currency,
        provider_status=provider_status,
        safe_payload=safe_payload or {},
    )
    session.add(tx)
    order.status = ORDER_PENDING
    order.payment_provider = provider
    await session.flush()
    return tx


async def find_transaction(
    session: AsyncSession, *, provider: str, provider_payment_id: str
) -> PaymentTransaction | None:
    return await session.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.provider == provider,
            PaymentTransaction.provider_payment_id == provider_payment_id,
        )
    )


def _owner_lookup(order: PaymentOrder) -> dict:
    if order.target_startup_id is not None:
        return {"startup_id": order.target_startup_id}
    if order.target_organization_id is not None:
        return {"organization_id": order.target_organization_id}
    return {"user_id": order.user_id}


async def activate_order(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    provider: str,
    provider_payment_id: str,
    charge_id: str | None,
    amount_minor: int,
    currency: str,
    provider_status: str = "succeeded",
) -> tuple[PaymentOrder, PremiumSubscription | None, bool]:
    """Confirm a payment and grant/extend Premium exactly once.

    Returns (order, subscription, created_grant).
    """
    order = await session.get(PaymentOrder, order_id, with_for_update=True)
    if order is None:
        raise PaymentError("Заказ не найден.", code="order_missing")
    if order.status == ORDER_PAID:
        tx = await find_transaction(
            session, provider=provider, provider_payment_id=provider_payment_id
        )
        if tx is not None and tx.order_id != order.id:
            raise PaymentError(
                "Платёж относится к другому заказу.", code="payment_order_mismatch"
            )
        sub = (
            await session.get(PremiumSubscription, order.premium_subscription_id)
            if order.premium_subscription_id
            else None
        )
        return order, sub, False
    if provider_status not in PAID_STATUSES:
        raise PaymentError("Платёж ещё не подтверждён.", code="not_paid")
    if amount_minor != order.amount_minor:
        raise PaymentError("Сумма платежа не совпадает с заказом.", code="amount_mismatch")
    if currency != order.currency:
        raise PaymentError("Валюта платежа не совпадает с заказом.", code="currency_mismatch")
    if order.status in {ORDER_CANCELED, ORDER_EXPIRED, ORDER_REFUNDED}:
        raise PaymentError("Заказ больше не может быть оплачен.", code="order_closed")

    # The provider payment must belong to this order (defence against a webhook
    # for a different, unrelated payment that carries our order metadata).
    tx = await find_transaction(
        session, provider=provider, provider_payment_id=provider_payment_id
    )
    if tx is not None and tx.order_id != order.id:
        raise PaymentError("Платёж относится к другому заказу.", code="payment_order_mismatch")

    premium = PremiumService(session)
    lookup = _owner_lookup(order)
    try:
        active = await premium.active_subscription(order.plan_code, **lookup)
        if active is not None:
            expires_before = active.expires_at
            sub = await premium.extend(
                active,
                order.duration_code,
                acting_user_id=order.user_id,
                reason=f"payment_order:{order.id}",
            )
            created = False
            order.subscription_extended = True
            order.subscription_expires_before = expires_before
            order.subscription_expires_after = sub.expires_at
        else:
            rub = rub_offer(order.plan_code, order.duration_code)
            sub, created = await premium.grant(
                plan_code=order.plan_code,
                duration_code=order.duration_code,
                acting_user_id=order.user_id,
                startup_id=order.target_startup_id,
                organization_id=order.target_organization_id,
                idempotency_key=f"payment_order:{order.id}",
                is_test=order.is_test,
                source=f"payment:{provider}",
                test_reason="provider_test_payment" if order.is_test else None,
                price_minor=rub.amount_minor if rub else 0,
            )
            order.subscription_extended = False
            order.subscription_expires_before = None
            order.subscription_expires_after = sub.expires_at
    except PremiumServiceError as exc:
        raise PaymentError(exc.message, code=exc.code) from exc

    now = datetime.now(timezone.utc)
    order.status = ORDER_PAID
    order.paid_at = now
    order.premium_subscription_id = sub.id
    # НПД receipts are not generated by YooKassa anymore: keep an explicit ledger.
    from raundo.services.tax_receipts import ensure_pending_receipt

    await ensure_pending_receipt(session, order)
    if tx is None:
        tx = PaymentTransaction(
            id=uuid.uuid4(),
            order_id=order.id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            amount_minor=amount_minor,
            currency=currency,
            provider_status=provider_status,
        )
        session.add(tx)
    tx.provider_status = provider_status
    tx.provider_charge_id = charge_id
    tx.processed_at = now
    tx.safe_payload = {"order_number": order.order_number}

    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=None,
            action="payment_activated",
            resource_type="payment_order",
            resource_id=str(order.id),
            before_state={"status": ORDER_PENDING},
            after_state={
                "status": ORDER_PAID,
                "provider": provider,
                "plan": order.plan_code,
                "duration": order.duration_code,
                "amount_minor": order.amount_minor,
                "currency": order.currency,
            },
            reason=None,
        )
    )
    await NotificationService(session).enqueue(
        kind="premium.purchased",
        dedupe_key=f"premium_purchased:{order.id}",
        recipient_user_id=order.user_id,
        resource_type="payment_order",
        resource_id=str(order.id),
        safe_payload={
            "plan": PLAN_TITLES.get(order.plan_code, order.plan_code),
            "period": order.duration_code,
            "until": sub.expires_at.strftime("%d.%m.%Y"),
        },
    )
    await session.flush()
    return order, sub, created


async def mark_order_failed(
    session: AsyncSession, *, provider: str, provider_payment_id: str, status: str
) -> PaymentOrder | None:
    tx = await find_transaction(session, provider=provider, provider_payment_id=provider_payment_id)
    if tx is None:
        return None
    tx.provider_status = status
    order = await session.get(PaymentOrder, tx.order_id)
    if order is None:
        return None
    if order.status == ORDER_PENDING and status == "canceled":
        order.status = ORDER_CANCELED
    await session.flush()
    return order


async def expire_overdue(session: AsyncSession, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = await session.execute(
        select(PaymentOrder).where(
            PaymentOrder.status.in_({ORDER_CREATED, ORDER_PENDING}),
            PaymentOrder.expires_at <= now,
        )
    )
    closed = 0
    for order in rows.scalars():
        order.status = ORDER_EXPIRED
        closed += 1
    if closed:
        await session.flush()
    return closed


async def order_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(PaymentOrder)) or 0)


async def rollback_entitlement(session: AsyncSession, order: PaymentOrder) -> None:
    """Remove exactly the paid time this refunded purchase added.

    Refunds can arrive in any order, including the very first purchase of a chain
    that later purchases extended. Restoring a stored snapshot would then delete
    time paid by those later purchases, so the rollback subtracts this order's
    duration from the subscription window instead:

    * purchase created the only paid window -> the subscription is cancelled;
    * purchase extended an existing window -> expiry moves back by its own hours;
    * other paid orders (earlier, later or another plan) are never touched.

    Used by every provider (Telegram Stars and YooKassa), so a refund always has
    the same entitlement effect.
    """
    if not order.premium_subscription_id:
        return
    sub = await session.get(PremiumSubscription, order.premium_subscription_id)
    if sub is None:
        return
    hours = DURATION_HOURS.get(order.duration_code)
    if hours is None:
        return
    other_paid = await session.scalar(
        select(func.count())
        .select_from(PaymentOrder)
        .where(
            PaymentOrder.premium_subscription_id == sub.id,
            PaymentOrder.id != order.id,
            PaymentOrder.status == ORDER_PAID,
        )
    )
    now = datetime.now(timezone.utc)
    if not order.subscription_extended and not other_paid:
        # Nothing was bought before this order and nothing was bought after it:
        # the refund ends the entitlement entirely.
        sub.status = SUB_STATUS_CANCELLED
        await session.flush()
        return

    expires = ensure_utc(sub.expires_at)
    if expires is not None:
        sub.expires_at = expires - timedelta(hours=hours)
    remaining = ensure_utc(sub.expires_at)
    sub.status = (
        SUB_STATUS_ACTIVE
        if remaining is not None and remaining > now
        else SUB_STATUS_EXPIRED
    )
    await session.flush()


async def refund_order(
    session: AsyncSession,
    *,
    order: PaymentOrder,
    refund_status: str,
    reason: str | None = None,
    transaction: PaymentTransaction | None = None,
) -> PaymentOrder:
    if order.status != ORDER_PAID:
        raise PaymentError("Возврат возможен только для оплаченного заказа.", code="not_paid")
    order.status = ORDER_REFUNDED
    tx = transaction
    if tx is None:
        # An order normally has one successful transaction; pick the latest so a
        # cancelled attempt can never be mistaken for the paid one.
        tx = await session.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.order_id == order.id)
            .order_by(PaymentTransaction.created_at.desc())
            .limit(1)
        )
    if tx is not None:
        tx.refund_status = refund_status
        tx.refunded_at = datetime.now(timezone.utc)
    await rollback_entitlement(session, order)
    # The self-employed receipt ledger follows the refund (no receipt is deleted).
    from raundo.services.tax_receipts import mark_refunded

    await mark_refunded(session, order.id)
    session.add(
        AdminAuditLog(
            id=uuid.uuid4(),
            actor_id=None,
            action="payment_refunded",
            resource_type="payment_order",
            resource_id=str(order.id),
            before_state={"status": ORDER_PAID},
            after_state={"status": ORDER_REFUNDED, "refund_status": refund_status},
            reason=reason,
        )
    )
    await NotificationService(session).enqueue(
        kind="payment.refunded",
        dedupe_key=f"refund:{order.id}",
        recipient_user_id=order.user_id,
        resource_type="payment_order",
        resource_id=str(order.id),
        safe_payload={"order_number": order.order_number},
    )
    await session.flush()
    return order


def stars_callbacks_enabled(settings: Settings) -> bool:
    return settings.payments_stars_enabled and (
        settings.payments_mode != "live"
        or (settings.payments_stars_prices_approved and settings.payments_legal_ready)
    )
