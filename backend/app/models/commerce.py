"""Authoritative quote, reservation, Razorpay, webhook, and audit persistence."""

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


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ProviderOrderOperationState(StrEnum):
    CREATING = "CREATING"
    CREATED = "CREATED"
    UNKNOWN = "UNKNOWN"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    UNKNOWN = "UNKNOWN"


class WebhookProcessingStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class PurchaseQuote(TimestampMixin, Base):
    """Immutable commercial and product snapshot selected by the agent."""

    __tablename__ = "purchase_quotes"
    __table_args__ = (
        UniqueConstraint("purchase_run_id", name="uq_purchase_quotes_purchase_run_id"),
        UniqueConstraint("quote_hash", name="uq_purchase_quotes_quote_hash"),
        CheckConstraint("product_version >= 1", name="product_version_positive"),
        CheckConstraint("controls_version >= 1", name="controls_version_positive"),
        CheckConstraint("unit_amount_paise > 0", name="unit_amount_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("total_amount_paise > 0", name="total_amount_positive"),
        CheckConstraint(
            "total_amount_paise = unit_amount_paise * quantity",
            name="total_matches_unit_quantity",
        ),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "selection_source IN ('openrouter', 'deterministic', 'deterministic_fallback')",
            name="selection_source_valid",
        ),
        Index("ix_purchase_quotes_product_created", "product_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    merchant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    controls_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    total_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    selection_source: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_reason: Mapped[str] = mapped_column(Text, nullable=False)
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    comparison_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PurchaseReservation(TimestampMixin, Base):
    """Expiring stock and budget hold consumed only after provider capture."""

    __tablename__ = "purchase_reservations"
    __table_args__ = (
        UniqueConstraint(
            "purchase_run_id", name="uq_purchase_reservations_purchase_run_id"
        ),
        UniqueConstraint("quote_id", name="uq_purchase_reservations_quote_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint(
            "status IN ('ACTIVE', 'CAPTURED', 'RELEASED', 'EXPIRED')",
            name="status_valid",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_purchase_reservations_status_expiry", "status", "expires_at"),
        Index("ix_purchase_reservations_product_status", "product_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=False
    )
    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_quotes.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ReservationStatus.ACTIVE.value,
        server_default=ReservationStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RazorpayOrder(TimestampMixin, Base):
    """Local source of truth for one Razorpay Order creation operation."""

    __tablename__ = "razorpay_orders"
    __table_args__ = (
        UniqueConstraint("purchase_run_id", name="uq_razorpay_orders_purchase_run_id"),
        UniqueConstraint("quote_id", name="uq_razorpay_orders_quote_id"),
        UniqueConstraint("provider_order_id", name="uq_razorpay_orders_provider_order_id"),
        UniqueConstraint("receipt", name="uq_razorpay_orders_receipt"),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "operation_state IN ('CREATING', 'CREATED', 'UNKNOWN')",
            name="operation_state_valid",
        ),
        Index("ix_razorpay_orders_provider_status", "provider_status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=False
    )
    quote_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_quotes.id", ondelete="RESTRICT"), nullable=False
    )
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    operation_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ProviderOrderOperationState.CREATING.value,
        server_default=ProviderOrderOperationState.CREATING.value,
    )
    provider_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_notes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class PaymentAttempt(TimestampMixin, Base):
    """Provider-confirmed payment facts associated with a Razorpay Order."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "provider_payment_id", name="uq_payment_attempts_provider_payment_id"
        ),
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint(
            "status IN ('CREATED', 'AUTHORIZED', 'CAPTURED', 'FAILED', "
            "'REFUNDED', 'UNKNOWN')",
            name="status_valid",
        ),
        Index("ix_payment_attempts_run_created", "purchase_run_id", "created_at"),
        Index("ix_payment_attempts_order_status", "razorpay_order_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=False
    )
    razorpay_order_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("razorpay_orders.id", ondelete="RESTRICT"), nullable=False
    )
    provider_payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PaymentStatus.CREATED.value
    )
    captured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookEvent(TimestampMixin, Base):
    """Deduplicated, signature-verified webhook processing inbox."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider_event_id", name="uq_webhook_events_provider_event_id"
        ),
        CheckConstraint(
            "processing_status IN ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')",
            name="processing_status_valid",
        ),
        Index("ix_webhook_events_status_created", "processing_status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    purchase_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=True
    )
    provider_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    processing_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=WebhookProcessingStatus.RECEIVED.value,
        server_default=WebhookProcessingStatus.RECEIVED.value,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEntry(Base):
    """Append-only, hash-chained explanation for every purchase money action."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        UniqueConstraint(
            "purchase_run_id", "sequence_number", name="uq_audit_entries_run_sequence"
        ),
        CheckConstraint("sequence_number > 0", name="sequence_positive"),
        Index("ix_audit_entries_run_created", "purchase_run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchase_runs.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
