"""Add authoritative quotes, reservations, provider records, webhooks, and audit.

Revision ID: 20260903_0007
Revises: 20260903_0006
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0007"
down_revision: str | None = "20260903_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.add_column("purchase_runs", sa.Column("request_hash", sa.String(length=64)))
    op.add_column(
        "purchase_runs",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_purchase_runs_version_positive"),
        "purchase_runs",
        "version >= 1",
    )

    op.create_table(
        "purchase_quotes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("product_version", sa.Integer(), nullable=False),
        sa.Column("controls_version", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=180), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("unit_amount_paise", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("total_amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("selection_source", sa.String(length=32), nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.Column(
            "product_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "comparison_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "controls_version >= 1",
            name=op.f("ck_purchase_quotes_controls_version_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'INR'", name=op.f("ck_purchase_quotes_currency_inr")
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_purchase_quotes_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "product_version >= 1",
            name=op.f("ck_purchase_quotes_product_version_positive"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_purchase_quotes_quantity_positive")
        ),
        sa.CheckConstraint(
            "selection_source IN ('openrouter', 'deterministic', 'deterministic_fallback')",
            name=op.f("ck_purchase_quotes_selection_source_valid"),
        ),
        sa.CheckConstraint(
            "total_amount_paise = unit_amount_paise * quantity",
            name=op.f("ck_purchase_quotes_total_matches_unit_quantity"),
        ),
        sa.CheckConstraint(
            "total_amount_paise > 0",
            name=op.f("ck_purchase_quotes_total_amount_positive"),
        ),
        sa.CheckConstraint(
            "unit_amount_paise > 0",
            name=op.f("ck_purchase_quotes_unit_amount_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_purchase_quotes_merchant_id_merchants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_purchase_quotes_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_purchase_quotes_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_quotes")),
        sa.UniqueConstraint(
            "purchase_run_id", name=op.f("uq_purchase_quotes_purchase_run_id")
        ),
        sa.UniqueConstraint("quote_hash", name=op.f("uq_purchase_quotes_quote_hash")),
    )
    op.create_index(
        "ix_purchase_quotes_product_created",
        "purchase_quotes",
        ["product_id", "created_at"],
    )

    op.create_table(
        "purchase_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="ACTIVE", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "amount_paise > 0",
            name=op.f("ck_purchase_reservations_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'INR'", name=op.f("ck_purchase_reservations_currency_inr")
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_purchase_reservations_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_purchase_reservations_quantity_positive")
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CAPTURED', 'RELEASED', 'EXPIRED')",
            name=op.f("ck_purchase_reservations_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_purchase_reservations_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_purchase_reservations_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["purchase_quotes.id"],
            name=op.f("fk_purchase_reservations_quote_id_purchase_quotes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_reservations")),
        sa.UniqueConstraint(
            "purchase_run_id", name=op.f("uq_purchase_reservations_purchase_run_id")
        ),
        sa.UniqueConstraint("quote_id", name=op.f("uq_purchase_reservations_quote_id")),
    )
    op.create_index(
        "ix_purchase_reservations_product_status",
        "purchase_reservations",
        ["product_id", "status"],
    )
    op.create_index(
        "ix_purchase_reservations_status_expiry",
        "purchase_reservations",
        ["status", "expires_at"],
    )

    op.create_table(
        "razorpay_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64)),
        sa.Column("receipt", sa.String(length=40), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column(
            "operation_state", sa.String(length=24), server_default="CREATING", nullable=False
        ),
        sa.Column("provider_status", sa.String(length=40)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_notes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "amount_paise > 0", name=op.f("ck_razorpay_orders_amount_positive")
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_razorpay_orders_attempts_non_negative")
        ),
        sa.CheckConstraint(
            "currency = 'INR'", name=op.f("ck_razorpay_orders_currency_inr")
        ),
        sa.CheckConstraint(
            "operation_state IN ('CREATING', 'CREATED', 'UNKNOWN')",
            name=op.f("ck_razorpay_orders_operation_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_razorpay_orders_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["purchase_quotes.id"],
            name=op.f("fk_razorpay_orders_quote_id_purchase_quotes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_razorpay_orders")),
        sa.UniqueConstraint(
            "provider_order_id", name=op.f("uq_razorpay_orders_provider_order_id")
        ),
        sa.UniqueConstraint(
            "purchase_run_id", name=op.f("uq_razorpay_orders_purchase_run_id")
        ),
        sa.UniqueConstraint("quote_id", name=op.f("uq_razorpay_orders_quote_id")),
        sa.UniqueConstraint("receipt", name=op.f("uq_razorpay_orders_receipt")),
    )
    op.create_index(
        "ix_razorpay_orders_provider_status",
        "razorpay_orders",
        ["provider_status", "created_at"],
    )

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_order_id", sa.Uuid(), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("captured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("payment_method", sa.String(length=40)),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_description", sa.Text()),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_created_at", sa.DateTime(timezone=True)),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "amount_paise > 0", name=op.f("ck_payment_attempts_amount_positive")
        ),
        sa.CheckConstraint(
            "currency = 'INR'", name=op.f("ck_payment_attempts_currency_inr")
        ),
        sa.CheckConstraint(
            "status IN ('CREATED', 'AUTHORIZED', 'CAPTURED', 'FAILED', "
            "'REFUNDED', 'UNKNOWN')",
            name=op.f("ck_payment_attempts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_payment_attempts_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["razorpay_order_id"],
            ["razorpay_orders.id"],
            name=op.f("fk_payment_attempts_razorpay_order_id_razorpay_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint(
            "provider_payment_id", name=op.f("uq_payment_attempts_provider_payment_id")
        ),
    )
    op.create_index(
        "ix_payment_attempts_order_status",
        "payment_attempts",
        ["razorpay_order_id", "status"],
    )
    op.create_index(
        "ix_payment_attempts_run_created",
        "payment_attempts",
        ["purchase_run_id", "created_at"],
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid()),
        sa.Column("provider_order_id", sa.String(length=64)),
        sa.Column("provider_payment_id", sa.String(length=64)),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "payload_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "processing_status", sa.String(length=24), server_default="RECEIVED", nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_error", sa.Text()),
        *_timestamps(),
        sa.CheckConstraint(
            "processing_status IN ('RECEIVED', 'PROCESSED', 'IGNORED', 'FAILED')",
            name=op.f("ck_webhook_events_processing_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_webhook_events_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint(
            "provider_event_id", name=op.f("uq_webhook_events_provider_event_id")
        ),
    )
    op.create_index(
        "ix_webhook_events_status_created",
        "webhook_events",
        ["processing_status", "created_at"],
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_number > 0", name=op.f("ck_audit_entries_sequence_positive")
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_audit_entries_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entries")),
        sa.UniqueConstraint(
            "purchase_run_id",
            "sequence_number",
            name=op.f("uq_audit_entries_run_sequence"),
        ),
    )
    op.create_index(
        "ix_audit_entries_run_created",
        "audit_entries",
        ["purchase_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("audit_entries")
    op.drop_table("webhook_events")
    op.drop_table("payment_attempts")
    op.drop_table("razorpay_orders")
    op.drop_table("purchase_reservations")
    op.drop_table("purchase_quotes")
    op.drop_constraint(
        op.f("ck_purchase_runs_version_positive"),
        "purchase_runs",
        type_="check",
    )
    op.drop_column("purchase_runs", "version")
    op.drop_column("purchase_runs", "request_hash")
