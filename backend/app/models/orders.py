"""Buyer delivery addresses and multi-item cart orders.

The agent purchase flow (``purchase_runs`` / ``purchase_quotes``) is a
single-product, Razorpay-only rail governed by saved spend controls. Cart
checkout is a separate concern: several catalogue lines in one order, a physical
delivery address, and a buyer-selected payment method that may be cash on
delivery. These tables model that flow without weakening the agent's state
machine.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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


class PaymentMethod(StrEnum):
    COD = "COD"
    RAZORPAY = "RAZORPAY"


class OrderStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"


class OrderPaymentStatus(StrEnum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"


class DeliveryAddress(TimestampMixin, Base):
    """A reusable shipping destination owned by exactly one buyer."""

    __tablename__ = "delivery_addresses"
    __table_args__ = (
        CheckConstraint("length(TRIM(BOTH FROM full_name)) >= 2", name="full_name_present"),
        CheckConstraint("phone ~ '^[0-9]{10}$'", name="phone_ten_digits"),
        CheckConstraint("length(TRIM(BOTH FROM line1)) >= 4", name="line1_present"),
        CheckConstraint("length(TRIM(BOTH FROM city)) >= 2", name="city_present"),
        CheckConstraint("length(TRIM(BOTH FROM state)) >= 2", name="state_present"),
        CheckConstraint("postal_code ~ '^[1-9][0-9]{5}$'", name="postal_code_indian"),
        CheckConstraint("country = 'IN'", name="country_india"),
        Index("ix_delivery_addresses_user_created", "user_id", "created_at"),
        # At most one default address per buyer.
        Index(
            "uq_delivery_addresses_user_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default AND NOT is_archived"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(10), nullable=False)
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(160), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(6), nullable=False)
    country: Mapped[str] = mapped_column(
        String(2), nullable=False, default="IN", server_default="IN"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class CustomerOrder(TimestampMixin, Base):
    """A buyer-placed cart order with a frozen address and pricing snapshot."""

    __tablename__ = "customer_orders"
    __table_args__ = (
        UniqueConstraint("order_number", name="uq_customer_orders_order_number"),
        UniqueConstraint("provider_order_id", name="uq_customer_orders_provider_order_id"),
        UniqueConstraint("provider_payment_id", name="uq_customer_orders_provider_payment_id"),
        UniqueConstraint("receipt", name="uq_customer_orders_receipt"),
        CheckConstraint("item_count > 0", name="item_count_positive"),
        CheckConstraint("subtotal_paise > 0", name="subtotal_positive"),
        CheckConstraint("shipping_paise >= 0", name="shipping_non_negative"),
        CheckConstraint("total_paise > 0", name="total_positive"),
        CheckConstraint(
            "total_paise = subtotal_paise + shipping_paise", name="total_matches_components"
        ),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint("payment_method IN ('COD', 'RAZORPAY')", name="payment_method_valid"),
        CheckConstraint(
            "payment_status IN ('PENDING', 'PAID', 'FAILED')", name="payment_status_valid"
        ),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'PAYMENT_FAILED', 'CANCELLED')",
            name="status_valid",
        ),
        # Cash on delivery never begins in a payment-waiting state.
        CheckConstraint(
            "payment_method <> 'COD' OR status <> 'PENDING_PAYMENT'",
            name="cod_skips_payment_wait",
        ),
        # A confirmed prepaid order must have a verified, captured payment.
        CheckConstraint(
            "payment_method <> 'RAZORPAY' OR status <> 'CONFIRMED' "
            "OR (payment_status = 'PAID' AND provider_payment_id IS NOT NULL)",
            name="prepaid_confirmed_requires_payment",
        ),
        Index("ix_customer_orders_user_created", "user_id", "created_at"),
        Index("ix_customer_orders_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_number: Mapped[str] = mapped_column(String(24), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    delivery_address_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("delivery_addresses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Authoritative copy of the address as it was at placement time.
    shipping_address: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    payment_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OrderPaymentStatus.PENDING.value,
        server_default=OrderPaymentStatus.PENDING.value,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    receipt: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Guards against decrementing stock twice for the same order.
    inventory_committed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomerOrderItem(TimestampMixin, Base):
    """One catalogue line inside a cart order, priced by the server."""

    __tablename__ = "customer_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_customer_order_items_order_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_amount_paise > 0", name="unit_amount_positive"),
        CheckConstraint("line_total_paise > 0", name="line_total_positive"),
        CheckConstraint(
            "line_total_paise = unit_amount_paise * quantity", name="line_total_matches_unit"
        ),
        CheckConstraint("product_version >= 1", name="product_version_positive"),
        Index("ix_customer_order_items_order", "order_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customer_orders.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    merchant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
