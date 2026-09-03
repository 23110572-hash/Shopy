"""Merchant-owned technology catalogue model."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
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


class ProductCategory(StrEnum):
    SMARTPHONES = "smartphones"
    SPEAKERS = "speakers"
    HEADPHONES = "headphones"
    LAPTOPS = "laptops"
    TABLETS = "tablets"


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
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    brand: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[ProductCategory] = mapped_column(
        Enum(
            ProductCategory,
            name="product_category",
            values_callable=lambda enum: [item.value for item in enum],
            validate_strings=True,
        ),
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
