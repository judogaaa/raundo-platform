"""YooKassa adapter: official HTTP calls, idempotence and safe parsing."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from raundo.services.yookassa import YooKassaClient, YooKassaError


def _client(handler) -> YooKassaClient:  # noqa: ANN001
    return YooKassaClient(
        shop_id="shop-1",
        secret_key="secret-1",
        api_url="https://api.yookassa.test/v3",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_create_payment_uses_basic_auth_idempotence_and_snapshot() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["idem"] = request.headers.get("idempotence-key")
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "pay_123",
                "status": "pending",
                "paid": False,
                "amount": {"value": "299.00", "currency": "RUB"},
                "confirmation": {"type": "redirect", "confirmation_url": "https://pay.test/1"},
                "metadata": {"order_id": "order-1"},
                "test": True,
            },
        )

    client = _client(handler)
    payment = await client.create_payment(
        amount_minor=29900,
        currency="RUB",
        description="RAUNDO PAY-000001",
        return_url="https://checkout.test/checkout/token?returned=1",
        metadata={"order_id": "order-1"},
        idempotence_key="order:order-1",
    )
    assert payment.id == "pay_123"
    assert payment.amount_minor == 29900
    assert payment.confirmation_url == "https://pay.test/1"
    assert payment.test is True
    assert captured["url"].endswith("/v3/payments")
    expected = base64.b64encode(b"shop-1:secret-1").decode()
    assert captured["auth"] == f"Basic {expected}"
    assert captured["idem"] == "order:order-1"
    assert captured["json"]["capture"] is True
    assert captured["json"]["amount"] == {"value": "299.00", "currency": "RUB"}
    assert captured["json"]["confirmation"]["type"] == "redirect"


@pytest.mark.asyncio
async def test_get_payment_reads_real_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).endswith("/payments/pay_123")
        return httpx.Response(
            200,
            json={
                "id": "pay_123",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "1490.00", "currency": "RUB"},
                "metadata": {"order_id": "order-1"},
            },
        )

    payment = await _client(handler).get_payment("pay_123")
    assert payment.status == "succeeded"
    assert payment.paid is True
    assert payment.amount_minor == 149000


@pytest.mark.asyncio
async def test_provider_errors_are_mapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"type": "error"})

    with pytest.raises(YooKassaError):
        await _client(handler).create_payment(
            amount_minor=100,
            currency="RUB",
            description="x",
            return_url="https://x",
            metadata={},
            idempotence_key="k",
        )


def test_missing_credentials_raise_not_configured() -> None:
    with pytest.raises(YooKassaError) as exc:
        YooKassaClient(shop_id="", secret_key="")
    assert exc.value.code == "not_configured"


@pytest.mark.asyncio
async def test_get_refund_reads_authoritative_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).endswith("/refunds/ref_1")
        return httpx.Response(
            200,
            json={
                "id": "ref_1",
                "status": "succeeded",
                "payment_id": "pay_123",
                "amount": {"value": "299.00", "currency": "RUB"},
            },
        )

    refund = await _client(handler).get_refund("ref_1")
    assert refund.id == "ref_1"
    assert refund.status == "succeeded"
    assert refund.payment_id == "pay_123"
    assert refund.amount_minor == 29900
    assert refund.currency == "RUB"


@pytest.mark.asyncio
async def test_get_me_returns_only_safe_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/me")
        return httpx.Response(
            200,
            json={
                "account_id": "1517068",
                "test": False,
                "status": "enabled",
                "fiscalization_enabled": True,
                "itn": "must-not-be-returned",
                "payment_methods": ["bank_card", "sbp"],
            },
        )

    me = await _client(handler).get_me()
    assert me == {
        "account_id": "1517068",
        "test": False,
        "status": "enabled",
        "fiscalization_enabled": True,
        "payment_methods": ["bank_card", "sbp"],
    }
    assert "itn" not in me


@pytest.mark.asyncio
async def test_get_me_rejects_a_bad_key_safely() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"type": "error"})

    with pytest.raises(YooKassaError) as exc:
        await _client(handler).get_me()
    assert exc.value.code == "unauthorized"
