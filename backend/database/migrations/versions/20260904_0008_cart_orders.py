"""Add buyer delivery addresses and multi-item cart orders.

Additive only: creates ``delivery_addresses``, ``customer_orders``, and
``customer_order_items``. No existing table is altered, so the agent purchase
rail is untouched.

Revision ID: 20260904_0008
Revises: 20260903_0007
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0008"
down_revision: str | None = "20260903_0007"
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
    op.create_table(
        "delivery_addresses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=10), nullable=False),
        sa.Column("line1", sa.String(length=255), nullable=False),
        sa.Column("line2", sa.String(length=255), nullable=True),
        sa.Column("landmark", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=120), nullable=False),
        sa.Column("postal_code", sa.String(length=6), nullable=False),
        sa.Column("country", sa.String(length=2), server_default="IN", nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "length(TRIM(BOTH FROM city)) >= 2",
            name=op.f("ck_delivery_addresses_city_present"),
        ),
        sa.CheckConstraint(
            "country = 'IN'", name=op.f("ck_delivery_addresses_country_india")
        ),
        sa.CheckConstraint(
            "length(TRIM(BOTH FROM full_name)) >= 2",
            name=op.f("ck_delivery_addresses_full_name_present"),
        ),
        sa.CheckConstraint(
            "length(TRIM(BOTH FROM line1)) >= 4",
            name=op.f("ck_delivery_addresses_line1_present"),
        ),
        sa.CheckConstraint(
            "phone ~ '^[0-9]{10}$'", name=op.f("ck_delivery_addresses_phone_ten_digits")
        ),
        sa.CheckConstraint(
            "postal_code ~ '^[1-9][0-9]{5}$'",
            name=op.f("ck_delivery_addresses_postal_code_indian"),
        ),
        sa.CheckConstraint(
            "length(TRIM(BOTH FROM state)) >= 2",
            name=op.f("ck_delivery_addresses_state_present"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_delivery_addresses_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_addresses")),
    )
    op.create_index(
        "ix_delivery_addresses_user_created",
        "delivery_addresses",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_delivery_addresses_user_default",
        "delivery_addresses",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND NOT is_archived"),
    )

    op.create_table(
        "customer_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_number", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_address_id", sa.Uuid(), nullable=True),
        sa.Column(
            "shipping_address",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payment_method", sa.String(length=16), nullable=False),
        sa.Column(
            "payment_status", sa.String(length=16), server_default="PENDING", nullable=False
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("subtotal_paise", sa.Integer(), nullable=False),
        sa.Column(
            "shipping_paise", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("total_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("receipt", sa.String(length=40), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=True),
        sa.Column(
            "provider_signature_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "inventory_committed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "payment_method <> 'COD' OR status <> 'PENDING_PAYMENT'",
            name=op.f("ck_customer_orders_cod_skips_payment_wait"),
        ),
        sa.CheckConstraint(
            "currency = 'INR'", name=op.f("ck_customer_orders_currency_inr")
        ),
        sa.CheckConstraint(
            "item_count > 0", name=op.f("ck_customer_orders_item_count_positive")
        ),
        sa.CheckConstraint(
            "payment_method IN ('COD', 'RAZORPAY')",
            name=op.f("ck_customer_orders_payment_method_valid"),
        ),
        sa.CheckConstraint(
            "payment_status IN ('PENDING', 'PAID', 'FAILED')",
            name=op.f("ck_customer_orders_payment_status_valid"),
        ),
        sa.CheckConstraint(
            "payment_method <> 'RAZORPAY' OR status <> 'CONFIRMED' "
            "OR (payment_status = 'PAID' AND provider_payment_id IS NOT NULL)",
            name=op.f("ck_customer_orders_prepaid_confirmed_requires_payment"),
        ),
        sa.CheckConstraint(
            "shipping_paise >= 0", name=op.f("ck_customer_orders_shipping_non_negative")
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT', 'CONFIRMED', 'PAYMENT_FAILED', 'CANCELLED')",
            name=op.f("ck_customer_orders_status_valid"),
        ),
        sa.CheckConstraint(
            "subtotal_paise > 0", name=op.f("ck_customer_orders_subtotal_positive")
        ),
        sa.CheckConstraint(
            "total_paise = subtotal_paise + shipping_paise",
            name=op.f("ck_customer_orders_total_matches_components"),
        ),
        sa.CheckConstraint(
            "total_paise > 0", name=op.f("ck_customer_orders_total_positive")
        ),
        sa.ForeignKeyConstraint(
            ["delivery_address_id"],
            ["delivery_addresses.id"],
            name=op.f("fk_customer_orders_delivery_address_id_delivery_addresses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_customer_orders_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_orders")),
        sa.UniqueConstraint("order_number", name=op.f("uq_customer_orders_order_number")),
        sa.UniqueConstraint(
            "provider_order_id", name=op.f("uq_customer_orders_provider_order_id")
        ),
        sa.UniqueConstraint(
            "provider_payment_id", name=op.f("uq_customer_orders_provider_payment_id")
        ),
        sa.UniqueConstraint("receipt", name=op.f("uq_customer_orders_receipt")),
    )
    op.create_index(
        "ix_customer_orders_user_created", "customer_orders", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_customer_orders_status_created", "customer_orders", ["status", "created_at"]
    )

    op.create_table(
        "customer_order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("product_version", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=180), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("unit_amount_paise", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total_paise", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "line_total_paise = unit_amount_paise * quantity",
            name=op.f("ck_customer_order_items_line_total_matches_unit"),
        ),
        sa.CheckConstraint(
            "line_total_paise > 0",
            name=op.f("ck_customer_order_items_line_total_positive"),
        ),
        sa.CheckConstraint(
            "product_version >= 1",
            name=op.f("ck_customer_order_items_product_version_positive"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_customer_order_items_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_amount_paise > 0",
            name=op.f("ck_customer_order_items_unit_amount_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name=op.f("fk_customer_order_items_merchant_id_merchants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["customer_orders.id"],
            name=op.f("fk_customer_order_items_order_id_customer_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_customer_order_items_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_order_items")),
        sa.UniqueConstraint(
            "order_id", "product_id", name=op.f("uq_customer_order_items_order_id")
        ),
    )
    op.create_index("ix_customer_order_items_order", "customer_order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_order_items_order", table_name="customer_order_items")
    op.drop_table("customer_order_items")
    op.drop_index("ix_customer_orders_status_created", table_name="customer_orders")
    op.drop_index("ix_customer_orders_user_created", table_name="customer_orders")
    op.drop_table("customer_orders")
    op.drop_index("uq_delivery_addresses_user_default", table_name="delivery_addresses")
    op.drop_index("ix_delivery_addresses_user_created", table_name="delivery_addresses")
    op.drop_table("delivery_addresses")
