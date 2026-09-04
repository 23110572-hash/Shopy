"""Add address-bound Shopy Agent fulfilment orders.

Revision ID: 20260904_0010
Revises: 20260904_0009
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0010"
down_revision: str | None = "20260904_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_fulfillment_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(length=24), nullable=False),
        sa.Column("purchase_run_id", sa.Uuid(), nullable=False),
        sa.Column("quote_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_user_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_address_id", sa.Uuid(), nullable=False),
        sa.Column(
            "shipping_address",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column(
            "payment_provider",
            sa.String(length=16),
            server_default="razorpay",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="PENDING_PAYMENT",
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "amount_paise > 0",
            name=op.f("ck_agent_fulfillment_orders_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'INR'",
            name=op.f("ck_agent_fulfillment_orders_currency_inr"),
        ),
        sa.CheckConstraint(
            "payment_provider = 'razorpay'",
            name=op.f("ck_agent_fulfillment_orders_provider_razorpay"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'PAYMENT_UNKNOWN', 'PAYMENT_FAILED', "
            "'CONFIRMED', 'FULFILLMENT_REVIEW')",
            name=op.f("ck_agent_fulfillment_orders_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["buyer_user_id"],
            ["users.id"],
            name=op.f("fk_agent_fulfillment_orders_buyer_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_address_id"],
            ["delivery_addresses.id"],
            name=op.f(
                "fk_agent_fulfillment_orders_delivery_address_id_delivery_addresses"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_run_id"],
            ["purchase_runs.id"],
            name=op.f("fk_agent_fulfillment_orders_purchase_run_id_purchase_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["purchase_quotes.id"],
            name=op.f("fk_agent_fulfillment_orders_quote_id_purchase_quotes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_fulfillment_orders")),
        sa.UniqueConstraint(
            "order_number",
            name=op.f("uq_agent_fulfillment_orders_order_number"),
        ),
        sa.UniqueConstraint(
            "purchase_run_id",
            name=op.f("uq_agent_fulfillment_orders_purchase_run_id"),
        ),
        sa.UniqueConstraint(
            "quote_id",
            name=op.f("uq_agent_fulfillment_orders_quote_id"),
        ),
    )
    op.create_index(
        "ix_agent_fulfillment_orders_buyer_created",
        "agent_fulfillment_orders",
        ["buyer_user_id", "created_at"],
    )
    op.create_index(
        "ix_agent_fulfillment_orders_status_created",
        "agent_fulfillment_orders",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_fulfillment_orders_status_created",
        table_name="agent_fulfillment_orders",
    )
    op.drop_index(
        "ix_agent_fulfillment_orders_buyer_created",
        table_name="agent_fulfillment_orders",
    )
    op.drop_table("agent_fulfillment_orders")
