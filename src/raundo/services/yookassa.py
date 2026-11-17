"""Minimal YooKassa adapter (official HTTP API, server-side Basic auth).

Only the calls RAUNDO needs: create a redirect payment, fetch its real state for
webhook verification/reconciliation, and create a refund. Card data never
reaches RAUNDO: the user pays on YooKassa's hosted confirmation page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx


class YooKassaError(Exception):
    def __init__(self, message: str, *, code: str = "provider") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderPayment:
    id: str
    status: str
    paid: bool
    amount_minor: int
    currency: str
    confirmation_url: str | None
    metadata: dict = field(default_factory=dict)
    test: bool = False


@dataclass(frozen=True)
class ProviderRefund:
    id: str
    status: str
    payment_id: str
    amount_minor: int
    currency: str


def _minor(value: object) -> int:
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise YooKassaError("Некорректная сумма от провайдера.", code="amount") from exc


class YooKassaClient:
    def __init__(
        self,
        *,
        shop_id: str,
        secret_key: str,
        api_url: str = "https://api.yookassa.ru/v3",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not shop_id or not secret_key:
            raise YooKassaError("ЮKassa не настроена.", code="not_configured")
        self._auth = (shop_id, secret_key)
        self._api_url = api_url.rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self._auth,
            timeout=20.0,
            transport=self._transport,
            headers={"Content-Type": "application/json"},
        )

    def _payment(self, data: dict) -> ProviderPayment:
        amount = data.get("amount") or {}
        confirmation = data.get("confirmation") or {}
        return ProviderPayment(
            id=str(data.get("id") or ""),
            status=str(data.get("status") or ""),
            paid=bool(data.get("paid")),
            amount_minor=_minor(amount.get("value")),
            currency=str(amount.get("currency") or ""),
            confirmation_url=confirmation.get("confirmation_url"),
            metadata=data.get("metadata") or {},
            test=bool(data.get("test")),
        )

    async def create_payment(
        self,
        *,
        amount_minor: int,
        currency: str,
        description: str,
        return_url: str,
        metadata: dict,
        idempotence_key: str,
    ) -> ProviderPayment:
        payload = {
            "amount": {"value": f"{amount_minor / 100:.2f}", "currency": currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": {str(k): str(v) for k, v in metadata.items()},
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._api_url}/payments",
                    json=payload,
                    headers={"Idempotence-Key": idempotence_key},
                )
        except httpx.HTTPError as exc:
            raise YooKassaError("Платёжный провайдер недоступен.", code="network") from exc
        if response.status_code >= 400:
            raise YooKassaError("Провайдер отклонил создание платежа.", code="create")
        return self._payment(response.json())

    async def get_payment(self, payment_id: str) -> ProviderPayment:
        try:
            async with self._client() as client:
                response = await client.get(f"{self._api_url}/payments/{payment_id}")
        except httpx.HTTPError as exc:
            raise YooKassaError("Платёжный провайдер недоступен.", code="network") from exc
        if response.status_code == 404:
            raise YooKassaError("Платёж не найден.", code="missing")
        if response.status_code >= 400:
            raise YooKassaError("Не удалось проверить платёж.", code="fetch")
        return self._payment(response.json())

    async def get_refund(self, refund_id: str) -> ProviderRefund:
        """Authoritative refund state; used to verify refund.succeeded webhooks."""
        try:
            async with self._client() as client:
                response = await client.get(f"{self._api_url}/refunds/{refund_id}")
        except httpx.HTTPError as exc:
            raise YooKassaError("Платёжный провайдер недоступен.", code="network") from exc
        if response.status_code == 404:
            raise YooKassaError("Возврат не найден.", code="missing")
        if response.status_code >= 400:
            raise YooKassaError("Не удалось проверить возврат.", code="fetch")
        data = response.json()
        amount = data.get("amount") or {}
        return ProviderRefund(
            id=str(data.get("id") or ""),
            status=str(data.get("status") or ""),
            payment_id=str(data.get("payment_id") or ""),
            amount_minor=_minor(amount.get("value")),
            currency=str(amount.get("currency") or ""),
        )

    async def get_me(self) -> dict:
        """Safe shop check (read-only): no payment, refund or charge is created.

        Only non-sensitive fields are returned so the caller can log the result
        without risking credentials.
        """
        try:
            async with self._client() as client:
                response = await client.get(f"{self._api_url}/me")
        except httpx.HTTPError as exc:
            raise YooKassaError("Платёжный провайдер недоступен.", code="network") from exc
        if response.status_code == 401:
            raise YooKassaError("Ключ магазина отклонён.", code="unauthorized")
        if response.status_code >= 400:
            raise YooKassaError("Не удалось проверить магазин.", code="me")
        data = response.json()
        methods = data.get("payment_methods")
        return {
            "account_id": str(data.get("account_id") or ""),
            "test": bool(data.get("test")),
            "status": str(data.get("status") or ""),
            "fiscalization_enabled": bool(data.get("fiscalization_enabled")),
            "payment_methods": [str(item) for item in methods] if isinstance(methods, list) else [],
        }

    async def create_refund(
        self, *, payment_id: str, amount_minor: int, currency: str, idempotence_key: str
    ) -> dict:
        payload = {
            "payment_id": payment_id,
            "amount": {"value": f"{amount_minor / 100:.2f}", "currency": currency},
        }
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._api_url}/refunds",
                    json=payload,
                    headers={"Idempotence-Key": idempotence_key},
                )
        except httpx.HTTPError as exc:
            raise YooKassaError("Платёжный провайдер недоступен.", code="network") from exc
        if response.status_code >= 400:
            raise YooKassaError("Провайдер отклонил возврат.", code="refund")
        return response.json()
