"""Buyer-visible history for every governed Shopy Agent purchase run."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.orders import ShippingAddressSnapshot


class AgentRunHistoryItem(BaseModel):
    run_id: UUID
    conversation_id: UUID | None
    conversation_turn_id: UUID | None
    state: str
    payment_state: str | None
    terminal_reason: str | None
    provider_write_started: bool
    quote_id: UUID | None
    product_id: UUID | None
    product_title: str | None
    amount_paise: int | None = Field(default=None, gt=0)
    currency: Literal["INR"] = "INR"
    quote_expires_at: datetime | None
    fulfillment_order_id: UUID | None
    fulfillment_order_number: str | None
    fulfillment_status: str | None
    shipping_address: ShippingAddressSnapshot | None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    provider_order_id: str | None
    created_at: datetime
    updated_at: datetime


class AgentRunHistoryResponse(BaseModel):
    availability: Literal["available"] = "available"
    items: list[AgentRunHistoryItem] = Field(default_factory=list)
