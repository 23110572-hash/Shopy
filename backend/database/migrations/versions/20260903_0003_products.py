"""Create the merchant-owned technology catalogue.

Revision ID: 20260903_0003
Revises: 20260903_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRODUCT_CATEGORIES = ("smartphones", "speakers", "headphones", "laptops", "tablets")


def upgrade() -> None:
    category_enum = postgresql.ENUM(*PRODUCT_CATEGORIES, name="product_category")
    category_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=180), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(*PRODUCT_CATEGORIES, name="product_category", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("offer_price_paise", sa.Integer(), nullable=False),
        sa.Column("mrp_paise", sa.Integer(), nullable=True),
        sa.Column("inventory_quantity", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "specifications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "search_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("specifications_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.CheckConstraint("inventory_quantity >= 0", name="ck_products_inventory_non_negative"),
        sa.CheckConstraint(
            "mrp_paise IS NULL OR mrp_paise >= offer_price_paise",
            name="ck_products_mrp_not_below_offer",
        ),
        sa.CheckConstraint("offer_price_paise > 0", name="ck_products_offer_price_positive"),
        sa.CheckConstraint("source_url LIKE 'https://%'", name="ck_products_source_url_https"),
        sa.CheckConstraint("version >= 1", name="ck_products_version_positive"),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            name="fk_products_merchant_id_merchants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("merchant_id", "sku", name="uq_products_merchant_id_sku"),
    )
    op.create_index("ix_products_catalog_lookup", "products", ["category", "is_active"])
    op.create_index("ix_products_merchant_active", "products", ["merchant_id", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_products_merchant_active", table_name="products")
    op.drop_index("ix_products_catalog_lookup", table_name="products")
    op.drop_table("products")
    postgresql.ENUM(name="product_category").drop(op.get_bind(), checkfirst=True)
