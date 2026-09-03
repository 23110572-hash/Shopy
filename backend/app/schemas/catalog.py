"""Public catalogue response contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.product import ProductCategory


class CatalogProduct(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    brand: str
    model: str
    category: ProductCategory
    title: str
    description: str
    offer_price_paise: int = Field(gt=0)
    mrp_paise: int | None = Field(default=None, gt=0)
    inventory_quantity: int = Field(ge=0)
    in_stock: bool
    specifications: dict[str, Any]
    search_tags: list[str]
    image_url: str | None
    source_url: str
    specifications_verified_at: datetime
    version: int = Field(ge=1)


class CatalogPage(BaseModel):
    items: list[CatalogProduct]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    category_counts: dict[ProductCategory, int]
