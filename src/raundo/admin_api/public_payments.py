"""Public checkout and payment provider callbacks (no admin auth).

Routes here are the only public part of the Admin API process. In production a
reverse proxy should expose `/checkout*`, `/legal*` and `/api/public/*` only,
keeping the rest of the API private.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import Settings, get_settings
from raundo.db.session import get_session_factory
from raundo.models.payments import (
    ORDER_CANCELED,
    ORDER_EXPIRED,
    ORDER_PAID,
    ORDER_REFUNDED,
    PROVIDER_DEMO,
    PROVIDER_YOOKASSA,
    PaymentOrder,
)
from raundo.services import payments as payment_service
from raundo.services.yookassa import YooKassaClient, YooKassaError

router = APIRouter()

# Source checkouts resolve next to the repo; the container sets CHECKOUT_DIST
# because the package is installed into site-packages.
CHECKOUT_DIST = Path(
    os.environ.get("CHECKOUT_DIST")
    or (Path(__file__).resolve().parents[3] / "admin-web" / "dist")
)


async def db_session():
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


def yookassa_client(settings: Settings) -> YooKassaClient:
    """Adapter factory; tests monkeypatch this function."""
    return YooKassaClient(
        shop_id=settings.yookassa_shop_id,
        secret_key=settings.yookassa_key(),
        api_url=settings.yookassa_api_url,
    )


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _error(status: int, message: str) -> JSONResponse:
    return _private(JSONResponse({"error": message}, status_code=status))  # type: ignore[return-value]


async def _order(session: AsyncSession, token: str, *, lock: bool = False) -> PaymentOrder:
    order = await payment_service.get_order_by_token(session, token, for_update=lock)
    if order is None:
        raise HTTPException(status_code=404, detail="Ссылка не найдена.")
    if payment_service.token_expired(order):
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк.")
    return order


@router.get("/api/public/checkout/{token}")
async def checkout_view(
    token: str,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(db_session),
):
    order = await _order(session, token)
    payload = payment_service.public_order_payload(order, settings)
    if order.premium_subscription_id:
        from raundo.models.billing import PremiumSubscription

        sub = await session.get(PremiumSubscription, order.premium_subscription_id)
        if sub is not None:
            payload["paid_until"] = sub.expires_at.isoformat()
    return _private(JSONResponse(payload))  # type: ignore[return-value]


class PayBody(BaseModel):
    consent: bool = False


@router.post("/api/public/checkout/{token}/pay")
async def checkout_pay(
    token: str,
    body: PayBody,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(db_session),
):
    order = await _order(session, token, lock=True)
    if not body.consent:
        raise HTTPException(status_code=400, detail="Подтвердите условия подписки.")
    if order.status == ORDER_PAID:
        return _private(JSONResponse({"status": "paid"}))  # type: ignore[return-value]
    if order.status in {ORDER_CANCELED, ORDER_EXPIRED}:
        raise HTTPException(status_code=409, detail="Заказ больше не может быть оплачен.")
    if not order.consent_accepted_at:
        await payment_service.accept_consent(order, settings.payment_consent_version)
        await session.flush()

    if settings.payments_mode == "demo":
        # Demo deliberately never activates Premium: no money, no entitlement.
        tx = await payment_service.record_pending_transaction(
            session,
            order=order,
            provider=PROVIDER_DEMO,
            provider_payment_id=f"demo:{order.id}",
            amount_minor=order.amount_minor,
            currency=order.currency,
            provider_status="demo_pending",
            safe_payload={"mode": "demo"},
        )
        tx.safe_payload = {"mode": "demo", "message": "Тестовый режим: списание не выполняется."}
        await session.commit()
        return _private(  # type: ignore[return-value]
            JSONResponse(
                {
                    "status": "demo",
                    "paid": False,
                    "message": "Тестовый режим RAUNDO: реальная оплата не выполняется, "
                    "подписка не активируется.",
                }
            )
        )

    if not payment_service.provider_available(settings):
        raise HTTPException(status_code=503, detail="Оплата картой пока недоступна.")
    # The web channel is a separate owner switch: live Stars does not open card
    # sales until YooKassa notifications and a control payment are confirmed.
    if settings.payments_mode == "live" and not settings.site_checkout_enabled:
        raise HTTPException(status_code=503, detail="Продажи на сайте ещё не открыты.")
    if settings.payments_mode == "live" and not settings.payments_legal_ready:
        raise HTTPException(
            status_code=503,
            detail="Продажи ещё не открыты: документы и данные продавца не готовы.",
        )

    try:
        client = yookassa_client(settings)
        provider_payment = await client.create_payment(
            amount_minor=order.amount_minor,
            currency=order.currency,
            description=f"RAUNDO {order.order_number}",
            return_url=f"{(settings.checkout_base_url or '').rstrip('/')}/checkout/{token}?returned=1",
            metadata={"order_id": str(order.id), "order_number": order.order_number},
            idempotence_key=f"order:{order.id}",
        )
    except YooKassaError as exc:
        return _error(502, exc.message)
    if not provider_payment.id or not provider_payment.confirmation_url:
        return _error(502, "Провайдер не вернул ссылку на оплату.")
    await payment_service.record_pending_transaction(
        session,
        order=order,
        provider=PROVIDER_YOOKASSA,
        provider_payment_id=provider_payment.id,
        amount_minor=order.amount_minor,
        currency=order.currency,
        provider_status=provider_payment.status,
        safe_payload={
            "confirmation_url": provider_payment.confirmation_url,
            "test": provider_payment.test,
        },
    )
    await session.commit()
    return _private(  # type: ignore[return-value]
        JSONResponse(
            {
                "status": "pending",
                "confirmation_url": provider_payment.confirmation_url,
                "test": provider_payment.test,
            }
        )
    )


@router.get("/api/public/checkout/{token}/status")
async def checkout_status(
    token: str,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(db_session),
):
    order = await payment_service.get_order_by_token(session, token)
    if order is None:
        return _private(JSONResponse({"status": "not_found"}))  # type: ignore[return-value]
    payload = payment_service.public_order_payload(order, settings)
    if order.status == ORDER_PAID and order.premium_subscription_id:
        from raundo.models.billing import PremiumSubscription

        sub = await session.get(PremiumSubscription, order.premium_subscription_id)
        if sub is not None:
            payload["paid_until"] = sub.expires_at.isoformat()
    return _private(JSONResponse(payload))  # type: ignore[return-value]


async def _verified_order_for_payment(
    session: AsyncSession, payment
) -> tuple[PaymentOrder, object]:
    """Resolve the order from our own transaction ledger, never from the payload.

    The webhook body is untrusted; only the payment fetched from YooKassa with our
    credentials is authoritative. The provider payment must already belong to a
    pending transaction of this order, so a payment for another order (or another
    shop) can never activate anything here.
    """
    tx = await payment_service.find_transaction(
        session, provider=PROVIDER_YOOKASSA, provider_payment_id=payment.id
    )
    if tx is None:
        raise HTTPException(status_code=400, detail="Платёж не относится к заказу.")
    order = await session.get(PaymentOrder, tx.order_id, with_for_update=True)
    if order is None:
        raise HTTPException(status_code=400, detail="Заказ не найден.")
    if order.payment_provider != PROVIDER_YOOKASSA:
        raise HTTPException(status_code=400, detail="Способ оплаты заказа не совпадает.")
    metadata_order = (payment.metadata or {}).get("order_id")
    if metadata_order and str(metadata_order) != str(order.id):
        raise HTTPException(status_code=400, detail="Платёж не относится к заказу.")
    return order, tx


async def _handle_payment_event(
    session: AsyncSession, client: YooKassaClient, event_type: str, object_id: str
) -> dict:
    payment = await client.get_payment(object_id)
    if payment.id != object_id:
        raise HTTPException(status_code=400, detail="Платёж не найден.")

    if event_type == "payment.canceled" or payment.status == "canceled":
        await payment_service.mark_order_failed(
            session,
            provider=PROVIDER_YOOKASSA,
            provider_payment_id=payment.id,
            status="canceled",
        )
        await session.commit()
        return {"ok": True}

    if payment.status not in payment_service.PAID_STATUSES or not payment.paid:
        # pending / waiting_for_capture: nothing to grant yet, acknowledge.
        return {"ok": True, "pending": True}

    order, _tx = await _verified_order_for_payment(session, payment)
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
    except payment_service.PaymentError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {"ok": True}


async def _handle_refund_event(
    session: AsyncSession, client: YooKassaClient, event_type: str, object_id: str
) -> dict:
    refund = await client.get_refund(object_id)
    if refund.id != object_id:
        raise HTTPException(status_code=400, detail="Возврат не найден.")
    if event_type != "refund.succeeded" or refund.status != "succeeded":
        return {"ok": True, "ignored": True}
    if not refund.payment_id:
        raise HTTPException(status_code=400, detail="Возврат без платежа.")

    payment = await client.get_payment(refund.payment_id)
    order, tx = await _verified_order_for_payment(session, payment)
    if order.status == ORDER_REFUNDED:
        return {"ok": True}
    if order.status != ORDER_PAID:
        raise HTTPException(status_code=400, detail="Возврат по неоплаченному заказу.")
    if refund.currency != order.currency:
        raise HTTPException(status_code=400, detail="Валюта возврата не совпадает.")
    if refund.amount_minor > order.amount_minor:
        raise HTTPException(status_code=400, detail="Сумма возврата больше платежа.")
    if refund.amount_minor < order.amount_minor:
        # Partial refund: money is returned, the paid period is kept; support sees it.
        tx.refund_status = "partial"
        await session.commit()
        return {"ok": True, "partial": True}

    await payment_service.refund_order(
        session,
        order=order,
        refund_status="refunded",
        reason=f"yookassa_refund:{refund.id}",
        transaction=tx,
    )
    await session.commit()
    return {"ok": True}


@router.post("/api/public/payments/yookassa/webhook")
async def yookassa_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(db_session),
):
    # YooKassa does not sign webhooks with HMAC: the official verification is to
    # fetch the object by id from the API and trust only that response. Amounts,
    # statuses and order ownership are taken from the API response, never from
    # the posted body. Real notifications stay disabled in demo mode.
    if not payment_service.provider_available(settings) or settings.payments_mode == "demo":
        raise HTTPException(status_code=503, detail="ЮKassa не настроена.")
    try:
        event = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Некорректный запрос.") from None
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Некорректный запрос.")
    event_type = str(event.get("event") or "")
    obj = event.get("object")
    object_id = str(obj.get("id") or "") if isinstance(obj, dict) else ""
    if not object_id:
        raise HTTPException(status_code=400, detail="Нет идентификатора объекта.")

    client = yookassa_client(settings)
    try:
        if event_type.startswith("refund."):
            return await _handle_refund_event(session, client, event_type, object_id)
        if event_type.startswith("payment."):
            return await _handle_payment_event(session, client, event_type, object_id)
    except YooKassaError as exc:
        if exc.code == "missing":
            raise HTTPException(status_code=400, detail="Объект не найден.") from exc
        raise HTTPException(status_code=503, detail="Провайдер недоступен.") from exc

    # Unknown event types are acknowledged so YooKassa stops retrying them.
    return {"ok": True, "ignored": True}


def _landing_shell(status_code: int = 200) -> Response:
    """Public landing / static shell from the built checkout bundle."""
    page = CHECKOUT_DIST / "landing.html"
    if not page.exists():
        return HTMLResponse(
            "<h1>RAUNDO</h1><p>Страница ещё не собрана.</p>",
            status_code=503,
        )
    response = FileResponse(page, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/", include_in_schema=False)
async def public_landing() -> Response:
    """Brand landing for the public checkout host (no order data here)."""
    return _landing_shell()


@router.get("/logo.png", include_in_schema=False)
async def public_logo() -> Response:
    """Original RAUNDO logo, used by the landing page and as the favicon."""
    logo = CHECKOUT_DIST / "logo.png"
    if not logo.exists():
        raise HTTPException(status_code=404, detail="Not found")
    response = FileResponse(logo)
    response.headers["Cache-Control"] = "public, max-age=3600"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/favicon.ico", include_in_schema=False)
async def public_favicon() -> Response:
    return await public_logo()


@router.get("/checkout", include_in_schema=False)
async def checkout_page() -> Response:
    """A checkout link always carries a one-time token; bare /checkout goes home."""
    return RedirectResponse("/", status_code=302)


@router.get("/checkout/", include_in_schema=False)
async def checkout_page_slash() -> Response:
    return RedirectResponse("/", status_code=302)


@router.get("/checkout/{token}", include_in_schema=False)
async def checkout_page_with_token(
    token: str,
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Serve the checkout shell only for a real, non-expired order token.

    Unknown tokens answer 404 and expired ones 410; the page itself shows the
    matching state without ever asking the buyer to pick a plan again.
    """
    index = CHECKOUT_DIST / "checkout.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>Checkout is not built</h1><p>Run <code>npm --prefix checkout-web run build</code>.</p>",
            status_code=503,
        )
    order = await payment_service.get_order_by_token(session, token)
    if order is None:
        status_code = 404
    elif payment_service.token_expired(order):
        status_code = 410
    else:
        status_code = 200
    response = FileResponse(index, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
