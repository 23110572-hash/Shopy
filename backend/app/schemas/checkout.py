"""Typed contracts for genuine Razorpay Standard Checkout orchestration."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.agent import PurchaseProposal
from app.schemas.catalog import CatalogProduct
from app.schemas.orders import ShippingAddressSnapshot

CheckoutAction = Literal["CREATE_ORDER", "OPEN_CHECKOUT", "RECONCILE"]


def _default_checkout_actions() -> list[CheckoutAction]:
    return ["OPEN_CHECKOUT"]


class CreateCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    address_id: UUID


class CheckoutSessionResponse(BaseModel):
    run_id: UUID
    proposal_id: UUID
    fulfillment_order_id: UUID
    fulfillment_order_number: str
    fulfillment_status: str
    shipping_address: ShippingAddressSnapshot
    policy_snapshot: dict[str, Any]
    key_id: str
    order_id: str
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    merchant_name: str
    description: str
    prefill_name: str
    prefill_email: str
    prefill_contact: str
    state: Literal["ORDER_CREATED"] = "ORDER_CREATED"
    expires_at: datetime
    test_mode: Literal[True] = True
    allowed_actions: list[CheckoutAction] = Field(default_factory=_default_checkout_actions)


class CheckoutCallbackRequest(BaseModel):
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


class PostPurchaseCrossSellOffer(BaseModel):
    source_run_id: UUID
    source_product_title: str
    product: CatalogProduct
    product_version: int = Field(ge=1)
    relation_type: Literal["POST_PURCHASE_CROSS_SELL"] = "POST_PURCHASE_CROSS_SELL"
    benefit: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=800)


class PostPurchaseCrossSellDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ACCEPT", "DECLINE"]
    product_id: UUID
    product_version: int = Field(ge=1)


class PostPurchaseCrossSellDecisionResponse(BaseModel):
    source_run_id: UUID
    decision: Literal["ACCEPT", "DECLINE"]
    purchase_proposal: PurchaseProposal | None
    message: str


class PurchaseRunStatusResponse(BaseModel):
    run_id: UUID
    proposal_id: UUID
    state: str
    payment_state: str | None
    order_id: str | None
    payment_id: str | None
    provider_order_status: str | None
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    terminal_reason: str | None
    allowed_actions: list[CheckoutAction]
    quote_expires_at: datetime
    updated_at: datetime
    retry_after_ms: int | None = Field(default=None, ge=0)
    message: str
    fulfillment_order_id: UUID | None = None
    fulfillment_order_number: str | None = None
    fulfillment_status: str | None = None
    shipping_address: ShippingAddressSnapshot | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    post_purchase_offer: PostPurchaseCrossSellOffer | None = None
    post_purchase_proposal: PurchaseProposal | None = None


class RazorpayWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate", "ignored"]
