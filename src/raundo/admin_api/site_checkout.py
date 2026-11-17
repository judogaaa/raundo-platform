"""Self-service checkout on payments.example.invalid (disabled until provider approval).

Flow: Telegram Login Widget -> verified server-side -> signed session cookie ->
choose plan -> server-priced order -> payment (only when enabled) -> Premium is
activated by the existing idempotent `activate_order` path after a confirmed
provider webhook.

Nothing here creates a payment in demo mode; `SITE_CHECKOUT_ENABLED` gates the
whole surface.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import Settings, get_settings
from raundo.db.session import get_session_factory
from raundo.models.payments import ORDER_PENDING, PROVIDER_YOOKASSA, PaymentTransaction
from raundo.models.user import User
from raundo.services import payments as payment_service
from raundo.services.accounts import AccountService
from raundo.services.age import is_adult
from raundo.services.consents import ConsentService
from raundo.services.telegram_login import (
    TelegramLoginError,
    issue_session,
    read_session,
    verify_telegram_login,
)
from raundo.services.yookassa import YooKassaClient, YooKassaError

COOKIE = "raundo_site"


async def _session():  # pragma: no cover - FastAPI dependency
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


def _require_enabled(settings: Settings) -> None:
    if not settings.site_checkout_enabled:
        raise HTTPException(
            status_code=403,
            detail="Покупка на сайте временно недоступна: платёжный сценарий на согласовании.",
        )


async def _enabled_dependency(settings: Settings = Depends(get_settings)) -> None:
    # Runs before the DB session is created: a disabled surface never touches the DB.
    _require_enabled(settings)


# Router-level gate: disabled checkout is rejected before any DB dependency runs.
router = APIRouter(dependencies=[Depends(_enabled_dependency)])


async def _current_user(
    request: Request, session: AsyncSession, settings: Settings
) -> User:
    raw = request.cookies.get(COOKIE, "")
    user_id = read_session(raw, settings) if raw else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется вход через Telegram.")
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=401, detail="Сессия недействительна.")
    user = await session.get(User, uid)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Сессия недействительна.")
    return user


class LoginBody(BaseModel):
    id: str
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: str
    hash: str


class OrderBody(BaseModel):
    plan_code: str
    duration_code: str
    accept_offer: bool = False


@router.post("/api/public/site/session")
async def site_login(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
):
    _require_enabled(settings)
    try:
        raw = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректные данные входа.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Некорректные данные входа.")
    try:
        payload = verify_telegram_login({k: str(v) for k, v in raw.items()}, settings)
    except TelegramLoginError as exc:
        raise HTTPException(status_code=401, detail=f"Telegram login rejected: {exc.code}") from exc

    user = await AccountService(session).bind_telegram_user(
        telegram_id=int(payload["id"]),
        username=payload.get("username") or None,
        first_name=payload.get("first_name") or None,
        last_name=payload.get("last_name") or None,
        language_code="ru",
    )
    await session.commit()
    token = issue_session(str(user.id), settings)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=True,
        max_age=7 * 24 * 3600,
    )
    return {
        "ok": True,
        "display_name": (user.first_name or "участник RAUNDO"),
        "age_confirmed": is_adult(user),
    }


@router.get("/api/public/site/me")
async def site_me(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
):
    _require_enabled(settings)
    user = await _current_user(request, session, settings)
    return {
        "ok": True,
        "age_confirmed": is_adult(user),
        "telegram_id_set": bool(user.telegram_id),
    }


@router.post("/api/public/site/orders")
async def site_create_order(
    body: OrderBody,
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
):
    _require_enabled(settings)
    user = await _current_user(request, session, settings)
    if not is_adult(user):
        raise HTTPException(status_code=403, detail="Premium доступен только с 18 лет.")
    if not body.accept_offer:
        raise HTTPException(status_code=400, detail="Подтвердите оферту и условия оплаты.")
    consents = ConsentService(session)
    if not await consents.has_required_consents(user.id):
        raise HTTPException(status_code=403, detail="Сначала примите обязательные согласия.")
    offer = payment_service.rub_offer(body.plan_code, body.duration_code)
    if offer is None:
        raise HTTPException(status_code=404, detail="Тариф не найден.")
    if settings.payments_mode != "live" or not settings.yookassa_configured():
        raise HTTPException(
            status_code=503,
            detail="Онлайн-оплата на сайте ещё не подключена.",
        )
    order, token = await payment_service.create_order(
        session,
        user=user,
        plan_code=offer.plan_code,
        duration_code=offer.duration_code,
        provider=payment_service.PROVIDER_YOOKASSA,
        settings=settings,
    )
    await session.commit()
    return {
        "ok": True,
        "order_number": order.order_number,
        "amount_minor": order.amount_minor,
        "currency": order.currency,
        "checkout_url": f"/checkout/{token}",
    }




async def _reconcile_pending(session: AsyncSession, settings: Settings, order) -> None:
    """Safety net: confirm a pending YooKassa payment on return.

    The webhook remains the primary path; this server-side provider check makes
    sure Premium is not lost when a notification is delayed or missed.
    """
    if order is None or order.status != ORDER_PENDING:
        return
    if order.payment_provider != PROVIDER_YOOKASSA:
        return
    tx = await session.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.order_id == order.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(1)
    )
    if tx is None or tx.provider != PROVIDER_YOOKASSA:
        return
    client = YooKassaClient(
        shop_id=settings.yookassa_shop_id,
        secret_key=settings.yookassa_key(),
        api_url=settings.yookassa_api_url,
    )
    try:
        payment = await client.get_payment(tx.provider_payment_id)
    except YooKassaError:
        return
    if payment.status not in payment_service.PAID_STATUSES or not payment.paid:
        return
    if str((payment.metadata or {}).get("order_id")) != str(order.id):
        return
    try:
        await payment_service.activate_order(
            session,
            order_id=order.id,
            provider=PROVIDER_YOOKASSA,
            provider_payment_id=payment.id,
            charge_id=None,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            provider_status="succeeded",
        )
        await session.commit()
    except payment_service.PaymentError:
        await session.rollback()


@router.get("/api/public/site/orders/latest")
async def site_latest_order(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(_session),
):
    _require_enabled(settings)
    user = await _current_user(request, session, settings)
    from raundo.models.payments import PaymentOrder

    order = await session.scalar(
        select(PaymentOrder)
        .where(PaymentOrder.user_id == user.id)
        .order_by(PaymentOrder.created_at.desc())
        .limit(1)
    )
    await _reconcile_pending(session, settings, order)
    if order is not None:
        await session.refresh(order)
    if order is None:
        return {"ok": True, "order": None}
    return {
        "ok": True,
        "order": {
            "order_number": order.order_number,
            "status": order.status,
            "amount_minor": order.amount_minor,
            "currency": order.currency,
            "plan_code": order.plan_code,
            "duration_code": order.duration_code,
        },
    }
