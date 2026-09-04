"""Public and agent-facing catalogue contracts."""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CATEGORY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_category_slug(value: str) -> str:
    normalized = value.strip().casefold()
    if not 1 <= len(normalized) <= 40 or _CATEGORY_SLUG.fullmatch(normalized) is None:
        raise ValueError("Category must be a lowercase catalogue slug")
    return normalized


class CatalogProduct(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    brand: str
    model: str
    category: str = Field(min_length=1, max_length=40)
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

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        return normalize_category_slug(value)


class CatalogPage(BaseModel):
    items: list[CatalogProduct]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    category_counts: dict[str, int]


class CatalogCategorySummary(BaseModel):
    slug: str = Field(min_length=1, max_length=40)
    display_name: str = Field(min_length=1, max_length=120)
    description: str
    aliases: list[str]
    facet_definitions: list[dict[str, Any]]
    active_product_count: int = Field(ge=0)


class CatalogCategoryList(BaseModel):
    items: list[CatalogCategorySummary]


class CatalogSearchDiagnostics(BaseModel):
    total_in_stock: int = Field(ge=0)
    category_matches: int = Field(ge=0)
    text_matches: int = Field(ge=0)
    eligible_matches: int = Field(ge=0)
    lowest_matching_price_paise: int | None = Field(default=None, gt=0)
    reason: str
    applied_categories: list[str]
    applied_query: str
