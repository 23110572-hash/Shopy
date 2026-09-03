"""Authenticated Razorpay Standard Checkout and signed webhook endpoints."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.config import Settings
from app.database import Database
from app.dependencies import get_database, get_runtime_settings
from app.schemas.checkout import (
    CheckoutCallbackRequest,
    CheckoutSessionResponse,
    CreateCheckoutRequest,
    PurchaseRunStatusResponse,
    RazorpayWebhookResponse,
)
from app.security import CsrfPrincipalDependency, CurrentPrincipalDependency
from app.services.checkout import CheckoutService, CheckoutServiceError

router = APIRouter(tags=["checkout"])
MAX_WEBHOOK_BODY_BYTES = 1_048_576
_IDEMPOTENCY_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)

DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=16, max_length=128),
]
WebhookSignatureHeader = Annotated[
    str,
    Header(alias="X-Razorpay-Signature", min_length=32, max_length=256),
]
WebhookEventIdHeader = Annotated[
    str,
    Header(alias="x-razorpay-event-id", min_length=1, max_length=128),
]


def _service(database: Database, settings: Settings) -> CheckoutService:
    return CheckoutService(database, settings)


def _raise_service_error(error: CheckoutServiceError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error)},
    ) from error


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not 16 <= len(normalized) <= 128 or any(
        character not in _IDEMPOTENCY_CHARACTERS for character in normalized
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_IDEMPOTENCY_KEY",
                "message": (
                    "Idempotency-Key must be 16-128 characters using letters, numbers, "
                    "hyphen, underscore, period, or colon"
                ),
            },
        )
    return normalized


def _is_unknown(result: PurchaseRunStatusResponse) -> bool:
    return result.state == "PAYMENT_UNKNOWN" or result.payment_state == "UNKNOWN"


def _webhook_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail={"code": "WEBHOOK_TOO_LARGE", "message": "Webhook body is too large"},
    )


async def _read_bounded_webhook_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_CONTENT_LENGTH",
                    "message": "Content-Length must be a non-negative integer",
                },
            ) from exc
        if declared_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_CONTENT_LENGTH",
                    "message": "Content-Length must be a non-negative integer",
                },
            )
        if declared_length > MAX_WEBHOOK_BODY_BYTES:
            raise _webhook_too_large()

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BODY_BYTES:
            raise _webhook_too_large()
        body.extend(chunk)
    return bytes(body)


@router.post(
    "/api/checkout/orders",
    response_model=CheckoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkout_order(
    payload: CreateCheckoutRequest,
    idempotency_key: IdempotencyKeyHeader,
    principal: CsrfPrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> CheckoutSessionResponse:
    try:
        return await _service(database, settings).create_order(
            buyer=principal.user,
            proposal_id=payload.proposal_id,
            idempotency_key=_normalize_idempotency_key(idempotency_key),
        )
    except CheckoutServiceError as error:
        _raise_service_error(error)


@router.get(
    "/api/checkout/runs/{run_id}",
    response_model=PurchaseRunStatusResponse,
)
async def get_checkout_status(
    run_id: UUID,
    principal: CurrentPrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> PurchaseRunStatusResponse:
    try:
        return await _service(database, settings).get_status(
            buyer_user_id=principal.user.id,
            run_id=run_id,
        )
    except CheckoutServiceError as error:
        _raise_service_error(error)


@router.post(
    "/api/checkout/runs/{run_id}/confirm",
    response_model=PurchaseRunStatusResponse,
)
async def confirm_checkout_payment(
    run_id: UUID,
    payload: CheckoutCallbackRequest,
    response: Response,
    principal: CsrfPrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> PurchaseRunStatusResponse:
    service = _service(database, settings)
    try:
        result = await service.confirm_payment(
            buyer_user_id=principal.user.id,
            run_id=run_id,
            callback=payload,
        )
    except CheckoutServiceError as error:
        if error.code != "PAYMENT_UNKNOWN" or error.status_code != status.HTTP_202_ACCEPTED:
            _raise_service_error(error)
        try:
            result = await service.get_status(
                buyer_user_id=principal.user.id,
                run_id=run_id,
            )
        except CheckoutServiceError as status_error:
            _raise_service_error(status_error)
    if _is_unknown(result):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post(
    "/api/checkout/runs/{run_id}/reconcile",
    response_model=PurchaseRunStatusResponse,
)
async def reconcile_checkout_payment(
    run_id: UUID,
    response: Response,
    principal: CsrfPrincipalDependency,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> PurchaseRunStatusResponse:
    try:
        result = await _service(database, settings).reconcile(
            buyer_user_id=principal.user.id,
            run_id=run_id,
        )
    except CheckoutServiceError as error:
        _raise_service_error(error)
    if _is_unknown(result):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post(
    "/api/webhooks/razorpay",
    response_model=RazorpayWebhookResponse,
)
async def process_razorpay_webhook(
    request: Request,
    signature: WebhookSignatureHeader,
    provider_event_id: WebhookEventIdHeader,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> RazorpayWebhookResponse:
    if not settings.razorpay_webhook_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "WEBHOOK_NOT_CONFIGURED",
                "message": "Razorpay webhook verification is not configured",
            },
        )
    if not settings.razorpay_api_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RAZORPAY_NOT_CONFIGURED",
                "message": "Razorpay provider reconciliation is not configured",
            },
        )

    raw_body = await _read_bounded_webhook_body(request)
    normalized_signature = signature.strip()
    normalized_event_id = provider_event_id.strip()
    if not 32 <= len(normalized_signature) <= 256 or not 1 <= len(normalized_event_id) <= 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_WEBHOOK_HEADERS", "message": "Webhook headers are invalid"},
        )

    try:
        return await _service(database, settings).process_webhook(
            raw_body=raw_body,
            signature=normalized_signature,
            provider_event_id=normalized_event_id,
        )
    except CheckoutServiceError as error:
        _raise_service_error(error)
