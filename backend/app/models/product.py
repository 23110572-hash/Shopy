"""Merchant-owned, data-defined product catalogue models."""

from datetime import datetime
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

CATEGORY_SLUG_MAX_LENGTH = 40
LEGACY_PRODUCT_CATEGORIES = (
    "smartphones",
    "speakers",
    "headphones",
    "laptops",
    "tablets",
)


class CatalogCategory(TimestampMixin, Base):
    """A catalogue-owned category and its LLM-readable discovery metadata."""

    __tablename__ = "catalog_categories"
    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'",
            name="slug_format",
        ),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
        Index("ix_catalog_categories_active_sort", "is_active", "sort_order"),
    )

    slug: Mapped[str] = mapped_column(String(CATEGORY_SLUG_MAX_LENGTH), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    aliases: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    facet_definitions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class CatalogCategoryRelation(TimestampMixin, Base):
    """Catalogue-authored compatibility between source and add-on categories."""

    __tablename__ = "catalog_category_relations"
    __table_args__ = (
        CheckConstraint(
            "source_category <> target_category",
            name="different_categories",
        ),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
        UniqueConstraint(
            "source_category",
            "target_category",
            "relation_type",
            name="uq_catalog_category_relations_source_target_type",
        ),
        Index(
            "ix_catalog_category_relations_active_source",
            "source_category",
            "relation_type",
            "is_active",
            "sort_order",
        ),
    )

    source_category: Mapped[str] = mapped_column(
        String(CATEGORY_SLUG_MAX_LENGTH),
        ForeignKey("catalog_categories.slug", ondelete="CASCADE"),
        primary_key=True,
    )
    target_category: Mapped[str] = mapped_column(
        String(CATEGORY_SLUG_MAX_LENGTH),
        ForeignKey("catalog_categories.slug", ondelete="CASCADE"),
        primary_key=True,
    )
    relation_type: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
        default="POST_PURCHASE_CROSS_SELL",
        server_default="POST_PURCHASE_CROSS_SELL",
    )
    benefit: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "sku", name="uq_products_merchant_id_sku"),
        CheckConstraint("offer_price_paise > 0", name="offer_price_positive"),
        CheckConstraint(
            "mrp_paise IS NULL OR mrp_paise >= offer_price_paise",
            name="mrp_not_below_offer",
        ),
        CheckConstraint("inventory_quantity >= 0", name="inventory_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("source_url LIKE 'https://%'", name="source_url_https"),
        Index("ix_products_catalog_lookup", "category", "is_active"),
        Index("ix_products_merchant_active", "merchant_id", "is_active"),
        Index(
            "ix_products_agent_filter",
            "is_active",
            "category",
            "offer_price_paise",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[str] = mapped_column(
        String(CATEGORY_SLUG_MAX_LENGTH),
        ForeignKey("catalog_categories.slug", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    offer_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    mrp_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inventory_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    specifications: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    search_tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    search_document: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    specifications_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def in_stock(self) -> bool:
        return self.is_active and self.inventory_quantity > 0
