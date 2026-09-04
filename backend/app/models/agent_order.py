"""Address-bound fulfilment records for governed Shopy Agent purchases."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentFulfillmentStatus(StrEnum):
    """Buyer-visible state of the address-bound agent order."""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CONFIRMED = "CONFIRMED"
    FULFILLMENT_REVIEW = "FULFILLMENT_REVIEW"


class AgentFulfillmentOrder(TimestampMixin, Base):
    """One immutable delivery projection for one agent purchase run and quote."""

    __tablename__ = "agent_fulfillment_orders"
    __table_args__ = (
        UniqueConstraint(
            "purchase_run_id",
            name="uq_agent_fulfillment_orders_purchase_run_id",
        ),
        UniqueConstraint("quote_id", name="uq_agent_fulfillment_orders_quote_id"),
        UniqueConstraint("order_number", name="uq_agent_fulfillment_orders_order_number"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint("payment_provider = 'razorpay'", name="provider_razorpay"),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'PAYMENT_UNKNOWN', 'PAYMENT_FAILED', "
            "'CONFIRMED', 'FULFILLMENT_REVIEW')",
            name="status_valid",
        ),
        Index("ix_agent_fulfillment_orders_buyer_created", "buyer_user_id", "created_at"),
        Index("ix_agent_fulfillment_orders_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_number: Mapped[str] = mapped_column(String(24), nullable=False)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("purchase_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("purchase_quotes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    delivery_address_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("delivery_addresses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    shipping_address: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
        server_default="INR",
    )
    payment_provider: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="razorpay",
        server_default="razorpay",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AgentFulfillmentStatus.PENDING_PAYMENT.value,
        server_default=AgentFulfillmentStatus.PENDING_PAYMENT.value,
    )
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
