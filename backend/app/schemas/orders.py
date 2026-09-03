"""Contracts for delivery addresses and cart-based customer orders."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PaymentMethodLiteral = Literal["COD", "RAZORPAY"]
OrderStatusLiteral = Literal["PENDING_PAYMENT", "CONFIRMED", "PAYMENT_FAILED", "CANCELLED"]
OrderPaymentStatusLiteral = Literal["PENDING", "PAID", "FAILED"]

# Cart orders are capped so a single request cannot fan out unbounded work.
MAX_CART_LINES = 20
MAX_LINE_QUANTITY = 10


def _collapse(value: str) -> str:
    return " ".join(value.split())


class DeliveryAddressInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=10, max_length=10)
    line1: str = Field(min_length=4, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    landmark: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=2, max_length=120)
    state: str = Field(min_length=2, max_length=120)
    postal_code: str = Field(min_length=6, max_length=6)
    is_default: bool = False

    @field_validator("full_name", "line1", "city", "state", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        return _collapse(value) if isinstance(value, str) else value

    @field_validator("line2", "landmark", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        collapsed = _collapse(value)
        return collapsed or None

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        digits = "".join(character for character in value if character.isdigit())
        # Accept +91 / 0 prefixed input but store the bare 10-digit subscriber number.
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        return digits

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not value.isdigit() or value[0] not in "6789":
            raise ValueError("Enter a valid 10-digit Indian mobile number")
        return value

    @field_validator("postal_code", mode="before")
    @classmethod
    def normalize_postal_code(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return "".join(character for character in value if character.isdigit())

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, value: str) -> str:
        if not value.isdigit() or value[0] == "0":
            raise ValueError("Enter a valid 6-digit Indian PIN code")
        return value


class DeliveryAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    phone: str
    line1: str
    line2: str | None
    landmark: str | None
    city: str
    state: str
    postal_code: str
    country: Literal["IN"]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class DeliveryAddressList(BaseModel):
    items: list[DeliveryAddressResponse]


class CartLineInput(BaseModel):
    """Client sends identity and quantity only; the server prices every line."""

    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(ge=1, le=MAX_LINE_QUANTITY)


class PlaceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address_id: UUID
    payment_method: PaymentMethodLiteral
    items: Annotated[list[CartLineInput], Field(min_length=1, max_length=MAX_CART_LINES)]

    @field_validator("items")
    @classmethod
    def reject_duplicate_products(cls, value: list[CartLineInput]) -> list[CartLineInput]:
        identifiers = {line.product_id for line in value}
        if len(identifiers) != len(value):
            raise ValueError("Each product may appear only once; combine quantities instead")
        return value


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: UUID
    sku: str
    title: str
    brand: str
    model: str
    category: str
    unit_amount_paise: int = Field(gt=0)
    quantity: int = Field(gt=0)
    line_total_paise: int = Field(gt=0)


class ShippingAddressSnapshot(BaseModel):
    full_name: str
    phone: str
    line1: str
    line2: str | None = None
    landmark: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = "IN"


class OrderResponse(BaseModel):
    id: UUID
    order_number: str
    status: OrderStatusLiteral
    payment_method: PaymentMethodLiteral
    payment_status: OrderPaymentStatusLiteral
    item_count: int = Field(gt=0)
    subtotal_paise: int = Field(gt=0)
    shipping_paise: int = Field(ge=0)
    total_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    shipping_address: ShippingAddressSnapshot
    items: list[OrderItemResponse]
    placed_at: datetime | None
    paid_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    message: str


class RazorpayOrderHandoff(BaseModel):
    """Everything the browser needs to open Razorpay Standard Checkout."""

    key_id: str
    provider_order_id: str
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    merchant_name: str
    description: str
    prefill_name: str
    prefill_email: str
    prefill_contact: str
    test_mode: Literal[True] = True


class PlaceOrderResponse(BaseModel):
    order: OrderResponse
    # Present only for RAZORPAY orders that still require payment.
    razorpay: RazorpayOrderHandoff | None = None


class ConfirmOrderPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_payment_id: str = Field(min_length=4, max_length=128)
    razorpay_order_id: str = Field(min_length=4, max_length=128)
    razorpay_signature: str = Field(min_length=32, max_length=256)

    @field_validator(
        "razorpay_payment_id",
        "razorpay_order_id",
        "razorpay_signature",
        mode="before",
    )
    @classmethod
    def strip_provider_value(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
