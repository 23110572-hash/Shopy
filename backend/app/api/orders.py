"""Cart checkout endpoints: delivery addresses and COD/Razorpay orders."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import Settings
from app.database import Database
from app.dependencies import get_database, get_runtime_settings
from app.schemas.orders import (
    ConfirmOrderPaymentRequest,
    DeliveryAddressInput,
    DeliveryAddressList,
    DeliveryAddressResponse,
    OrderListResponse,
    OrderResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
)
from app.security import CsrfPrincipalDependency, CurrentPrincipalDependency
from app.services.orders import OrderService, OrderServiceError

router = APIRouter(tags=["orders"])
DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def _service(database: Database, settings: Settings) -> OrderService:
    return OrderService(database, settings)


def _raise(error: OrderServiceError) -> NoReturn:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": str(error)},
    ) from error


@router.get("/api/account/addresses", response_model=DeliveryAddressList)
async def list_addresses(
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CurrentPrincipalDependency,
) -> DeliveryAddressList:
    addresses = await _service(database, settings).list_addresses(principal.user.id)
    return DeliveryAddressList(
        items=[DeliveryAddressResponse.model_validate(address) for address in addresses]
    )


@router.post(
    "/api/account/addresses",
    response_model=DeliveryAddressResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_address(
    payload: DeliveryAddressInput,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> DeliveryAddressResponse:
    try:
        address = await _service(database, settings).create_address(principal.user.id, payload)
    except OrderServiceError as error:
        _raise(error)
    return DeliveryAddressResponse.model_validate(address)


@router.put("/api/account/addresses/{address_id}", response_model=DeliveryAddressResponse)
async def update_address(
    address_id: UUID,
    payload: DeliveryAddressInput,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> DeliveryAddressResponse:
    try:
        address = await _service(database, settings).update_address(
            principal.user.id, address_id, payload
        )
    except OrderServiceError as error:
        _raise(error)
    return DeliveryAddressResponse.model_validate(address)


@router.delete(
    "/api/account/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_address(
    address_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> Response:
    try:
        await _service(database, settings).delete_address(principal.user.id, address_id)
    except OrderServiceError as error:
        _raise(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/orders",
    response_model=PlaceOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def place_order(
    payload: PlaceOrderRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> PlaceOrderResponse:
    try:
        return await _service(database, settings).place_order(
            buyer=principal.user, payload=payload
        )
    except OrderServiceError as error:
        _raise(error)


@router.get("/api/orders", response_model=OrderListResponse)
async def list_orders(
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CurrentPrincipalDependency,
) -> OrderListResponse:
    items = await _service(database, settings).list_orders(buyer_user_id=principal.user.id)
    return OrderListResponse(items=items)


@router.get("/api/orders/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CurrentPrincipalDependency,
) -> OrderResponse:
    try:
        return await _service(database, settings).get_order(
            buyer_user_id=principal.user.id, order_id=order_id
        )
    except OrderServiceError as error:
        _raise(error)


@router.post("/api/orders/{order_id}/confirm-payment", response_model=OrderResponse)
async def confirm_order_payment(
    order_id: UUID,
    payload: ConfirmOrderPaymentRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> OrderResponse:
    try:
        return await _service(database, settings).confirm_payment(
            buyer=principal.user, order_id=order_id, callback=payload
        )
    except OrderServiceError as error:
        _raise(error)


@router.post("/api/orders/{order_id}/reconcile", response_model=OrderResponse)
async def reconcile_order_payment(
    order_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> OrderResponse:
    try:
        return await _service(database, settings).reconcile(
            buyer=principal.user, order_id=order_id
        )
    except OrderServiceError as error:
        _raise(error)
