"""Razorpay Standard Checkout port and test-mode HTTP adapter."""

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, cast, runtime_checkable

import httpx

from app.config import Settings

RAZORPAY_API_BASE_URL = "https://api.razorpay.com"


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    order_id: str
    amount_paise: int
    amount_paid_paise: int
    amount_due_paise: int
    currency: str
    receipt: str | None
    status: str
    attempts: int
    notes: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    payment_id: str
    order_id: str | None
    amount_paise: int
    currency: str
    status: str
    captured: bool
    method: str | None
    error_code: str | None
    error_description: str | None
    created_at_epoch: int | None


class RazorpayGatewayError(RuntimeError):
    """Sanitized provider failure safe to expose to orchestration code."""


class RazorpayRejectedError(RazorpayGatewayError):
    """The provider definitively rejected a request."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RazorpayUnavailableError(RazorpayGatewayError):
    """A read operation could not reach the provider."""


class RazorpayAmbiguousWriteError(RazorpayGatewayError):
    """A provider write may have succeeded and must be reconciled, never retried blindly."""


@runtime_checkable
class StandardCheckoutRail(Protocol):
    @property
    def public_key_id(self) -> str: ...

    async def create_order(
        self,
        *,
        amount_paise: int,
        receipt: str,
        notes: dict[str, str],
    ) -> ProviderOrder: ...

    async def fetch_order(self, *, order_id: str) -> ProviderOrder: ...

    async def fetch_payment(self, *, payment_id: str) -> ProviderPayment: ...

    async def fetch_order_payments(self, *, order_id: str) -> list[ProviderPayment]: ...

    async def capture_payment(
        self, *, payment_id: str, amount_paise: int
    ) -> ProviderPayment: ...

    def verify_checkout_signature(
        self,
        *,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> bool: ...

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool: ...


class RazorpayStandardCheckoutGateway:
    """Single-attempt Razorpay REST adapter; POST failures remain explicitly ambiguous."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key_id, key_secret = settings.require_razorpay_api()
        self._key_id = key_id
        self._key_secret = key_secret.encode("utf-8")
        self._webhook_secret = (
            settings.razorpay_webhook_secret.get_secret_value().encode("utf-8")
            if settings.razorpay_webhook_secret is not None
            else None
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=RAZORPAY_API_BASE_URL,
            auth=httpx.BasicAuth(key_id, key_secret),
            timeout=httpx.Timeout(12.0, connect=5.0),
            headers={"Accept": "application/json", "User-Agent": "Shopy/0.1"},
        )

    @property
    def public_key_id(self) -> str:
        return self._key_id

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_order(
        self,
        *,
        amount_paise: int,
        receipt: str,
        notes: dict[str, str],
    ) -> ProviderOrder:
        if amount_paise < 100:
            raise ValueError("Razorpay order amount must be at least 100 paise")
        if len(receipt) > 40:
            raise ValueError("Razorpay receipt must not exceed 40 characters")
        payload = await self._request_json(
            "POST",
            "/v1/orders",
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": notes,
                "capture": "automatic",
            },
            provider_write=True,
        )
        return _parse_order(payload)

    async def fetch_order(self, *, order_id: str) -> ProviderOrder:
        payload = await self._request_json("GET", f"/v1/orders/{order_id}")
        return _parse_order(payload)

    async def fetch_payment(self, *, payment_id: str) -> ProviderPayment:
        payload = await self._request_json("GET", f"/v1/payments/{payment_id}")
        return _parse_payment(payload)

    async def fetch_order_payments(self, *, order_id: str) -> list[ProviderPayment]:
        payload = await self._request_json("GET", f"/v1/orders/{order_id}/payments")
        items = payload.get("items")
        if not isinstance(items, list):
            raise RazorpayUnavailableError("Razorpay returned an invalid payments response")
        return [_parse_payment(_as_payload(item)) for item in items]

    async def capture_payment(
        self,
        *,
        payment_id: str,
        amount_paise: int,
    ) -> ProviderPayment:
        payload = await self._request_json(
            "POST",
            f"/v1/payments/{payment_id}/capture",
            json={"amount": amount_paise, "currency": "INR"},
            provider_write=True,
        )
        return _parse_payment(payload)

    def verify_checkout_signature(
        self,
        *,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> bool:
        message = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(self._key_secret, message, sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        if self._webhook_secret is None:
            raise RuntimeError("Razorpay webhook secret is not configured")
        expected = hmac.new(self._webhook_secret, body, sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        provider_write: bool = False,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except (httpx.TimeoutException, httpx.RequestError):
            if provider_write:
                raise RazorpayAmbiguousWriteError(
                    "Razorpay write result is unknown; reconcile the original operation"
                ) from None
            raise RazorpayUnavailableError("Razorpay is temporarily unavailable") from None

        payload = _response_payload(response)
        if response.status_code >= 500:
            if provider_write:
                raise RazorpayAmbiguousWriteError(
                    "Razorpay write result is unknown; reconcile the original operation"
                )
            raise RazorpayUnavailableError("Razorpay is temporarily unavailable")
        if response.status_code >= 400:
            code, description = _provider_error(payload)
            raise RazorpayRejectedError(
                f"Razorpay rejected the request ({code}): {description}",
                status_code=response.status_code,
            )
        return payload


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        raw_payload: object = response.json()
    except ValueError:
        if response.is_error:
            return {}
        raise RazorpayUnavailableError("Razorpay returned a non-JSON response") from None
    return _as_payload(raw_payload)


def _as_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RazorpayUnavailableError("Razorpay returned an invalid response")
    return cast(dict[str, Any], value)


def _provider_error(payload: Mapping[str, Any]) -> tuple[str, str]:
    error = payload.get("error")
    if not isinstance(error, dict):
        return "provider_error", "The provider rejected the request"
    code = error.get("code")
    description = error.get("description")
    safe_code = code if isinstance(code, str) and code else "provider_error"
    safe_description = (
        description
        if isinstance(description, str) and description
        else "The provider rejected the request"
    )
    return safe_code[:120], safe_description[:500]


def _required_str(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise RazorpayUnavailableError(f"Razorpay response omitted {field}")
    return value


def _optional_str(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    return value if isinstance(value, str) and value else None


def _required_int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RazorpayUnavailableError(f"Razorpay response omitted {field}")
    return value


def _optional_int(payload: Mapping[str, Any], field: str) -> int | None:
    value = payload.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_notes(payload: Mapping[str, Any]) -> dict[str, str]:
    value = payload.get("notes")
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(note_value)
        for key, note_value in value.items()
        if isinstance(key, str) and isinstance(note_value, (str, int, float, bool))
    }


def _parse_order(payload: Mapping[str, Any]) -> ProviderOrder:
    return ProviderOrder(
        order_id=_required_str(payload, "id"),
        amount_paise=_required_int(payload, "amount"),
        amount_paid_paise=_required_int(payload, "amount_paid"),
        amount_due_paise=_required_int(payload, "amount_due"),
        currency=_required_str(payload, "currency"),
        receipt=_optional_str(payload, "receipt"),
        status=_required_str(payload, "status"),
        attempts=_required_int(payload, "attempts"),
        notes=_parse_notes(payload),
    )


def _parse_payment(payload: Mapping[str, Any]) -> ProviderPayment:
    captured = payload.get("captured")
    if not isinstance(captured, bool):
        raise RazorpayUnavailableError("Razorpay response omitted captured")
    return ProviderPayment(
        payment_id=_required_str(payload, "id"),
        order_id=_optional_str(payload, "order_id"),
        amount_paise=_required_int(payload, "amount"),
        currency=_required_str(payload, "currency"),
        status=_required_str(payload, "status"),
        captured=captured,
        method=_optional_str(payload, "method"),
        error_code=_optional_str(payload, "error_code"),
        error_description=_optional_str(payload, "error_description"),
        created_at_epoch=_optional_int(payload, "created_at"),
    )
