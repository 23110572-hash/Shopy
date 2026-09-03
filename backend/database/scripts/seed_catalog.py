"""Validate and idempotently import the verified 100-product catalogue."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Connection, create_engine, text

from app.config import get_settings

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "verified_tech_products.csv"
EXPECTED_CATEGORIES = {
    "smartphones": 20,
    "speakers": 20,
    "headphones": 20,
    "laptops": 20,
    "tablets": 20,
}
EXPECTED_FIELDS = {
    "sku",
    "brand",
    "model",
    "category",
    "title",
    "description",
    "offer_price_paise",
    "mrp_paise",
    "inventory_quantity",
    "is_active",
    "specifications_json",
    "search_tags",
    "image_url",
    "source_url",
    "specifications_verified_at",
}
SEED_ADMIN_EMAIL = "catalog-admin@mandateguard.local"
SEED_MERCHANT_SLUG = "mandateguard-tech"


@dataclass(frozen=True, slots=True)
class SeedProduct:
    sku: str
    brand: str
    model: str
    category: str
    title: str
    description: str
    offer_price_paise: int
    mrp_paise: int | None
    inventory_quantity: int
    is_active: bool
    specifications: dict[str, object]
    search_tags: list[str]
    image_url: str | None
    source_url: str
    specifications_verified_at: datetime

    def parameters(self, merchant_id: UUID) -> dict[str, object]:
        return {
            "id": uuid4(),
            "merchant_id": merchant_id,
            "sku": self.sku,
            "brand": self.brand,
            "model": self.model,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "offer_price_paise": self.offer_price_paise,
            "mrp_paise": self.mrp_paise,
            "inventory_quantity": self.inventory_quantity,
            "is_active": self.is_active,
            "specifications": json.dumps(self.specifications, sort_keys=True),
            "search_tags": json.dumps(self.search_tags),
            "image_url": self.image_url,
            "source_url": self.source_url,
            "specifications_verified_at": self.specifications_verified_at,
        }


def _required(row: dict[str, str | None], field: str, line_number: int) -> str:
    value = row.get(field)
    if value is None or not value.strip():
        raise ValueError(f"Line {line_number}: {field} is required")
    return value.strip()


def _positive_integer(raw: str, field: str, line_number: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"Line {line_number}: {field} must be an integer") from None
    if value <= 0:
        raise ValueError(f"Line {line_number}: {field} must be positive")
    return value


def _parse_row(row: dict[str, str | None], line_number: int) -> SeedProduct:
    category = _required(row, "category", line_number).lower()
    if category not in EXPECTED_CATEGORIES:
        raise ValueError(f"Line {line_number}: unsupported category {category}")

    offer_price = _positive_integer(
        _required(row, "offer_price_paise", line_number), "offer_price_paise", line_number
    )
    raw_mrp = (row.get("mrp_paise") or "").strip()
    mrp = _positive_integer(raw_mrp, "mrp_paise", line_number) if raw_mrp else None
    if mrp is not None and mrp < offer_price:
        raise ValueError(f"Line {line_number}: mrp_paise cannot be below offer_price_paise")

    try:
        inventory = int(_required(row, "inventory_quantity", line_number))
    except ValueError:
        raise ValueError(f"Line {line_number}: inventory_quantity must be an integer") from None
    if inventory < 0:
        raise ValueError(f"Line {line_number}: inventory_quantity cannot be negative")

    active_text = _required(row, "is_active", line_number).lower()
    if active_text not in {"true", "false"}:
        raise ValueError(f"Line {line_number}: is_active must be true or false")

    raw_specifications: Any = json.loads(_required(row, "specifications_json", line_number))
    if not isinstance(raw_specifications, dict):
        raise ValueError(f"Line {line_number}: specifications_json must be an object")
    specifications = {str(key): value for key, value in raw_specifications.items()}

    tags = [tag.strip().lower() for tag in _required(row, "search_tags", line_number).split("|")]
    if not tags or any(not tag for tag in tags):
        raise ValueError(f"Line {line_number}: search_tags contains an empty tag")

    source_url = _required(row, "source_url", line_number)
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError(f"Line {line_number}: source_url must be an absolute HTTPS URL")

    try:
        verified_at = datetime.fromisoformat(
            _required(row, "specifications_verified_at", line_number).replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError(
            f"Line {line_number}: specifications_verified_at must be ISO-8601"
        ) from None
    if verified_at.tzinfo is None:
        raise ValueError(f"Line {line_number}: specifications_verified_at needs a timezone")

    return SeedProduct(
        sku=_required(row, "sku", line_number).upper(),
        brand=_required(row, "brand", line_number),
        model=_required(row, "model", line_number),
        category=category,
        title=_required(row, "title", line_number),
        description=_required(row, "description", line_number),
        offer_price_paise=offer_price,
        mrp_paise=mrp,
        inventory_quantity=inventory,
        is_active=active_text == "true",
        specifications=specifications,
        search_tags=tags,
        image_url=(row.get("image_url") or "").strip() or None,
        source_url=source_url,
        specifications_verified_at=verified_at,
    )


def load_and_validate_catalog(path: Path = DATA_FILE) -> list[SeedProduct]:
    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or set(reader.fieldnames) != EXPECTED_FIELDS:
            raise ValueError("Catalogue CSV columns do not match the required schema")
        products = [_parse_row(row, line_number) for line_number, row in enumerate(reader, 2)]

    if len(products) != 100:
        raise ValueError(f"Initial catalogue must contain exactly 100 rows; found {len(products)}")
    sku_counts = Counter(product.sku for product in products)
    duplicate_skus = sorted(sku for sku, count in sku_counts.items() if count > 1)
    if duplicate_skus:
        raise ValueError(f"Duplicate SKUs: {', '.join(duplicate_skus)}")
    category_counts = Counter(product.category for product in products)
    if dict(category_counts) != EXPECTED_CATEGORIES:
        raise ValueError(
            f"Expected exactly 20 products per category; found {dict(category_counts)}"
        )
    return products


def _ensure_merchant(connection: Connection) -> UUID:
    admin_id = connection.scalar(
        text("SELECT id FROM users WHERE email = :email"), {"email": SEED_ADMIN_EMAIL}
    )
    if admin_id is None:
        admin_id = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, role)
                VALUES (:id, :email, :display_name, CAST(:role AS user_role))
                """
            ),
            {
                "id": admin_id,
                "email": SEED_ADMIN_EMAIL,
                "display_name": "MandateGuard Catalogue Admin",
                "role": "merchant_admin",
            },
        )
    if not isinstance(admin_id, UUID):
        admin_id = UUID(str(admin_id))

    merchant_id = connection.scalar(
        text("SELECT id FROM merchants WHERE slug = :slug"), {"slug": SEED_MERCHANT_SLUG}
    )
    if merchant_id is None:
        merchant_id = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO merchants (id, owner_user_id, name, slug)
                VALUES (:id, :owner_user_id, :name, :slug)
                """
            ),
            {
                "id": merchant_id,
                "owner_user_id": admin_id,
                "name": "MandateGuard Technology Store",
                "slug": SEED_MERCHANT_SLUG,
            },
        )
    return merchant_id if isinstance(merchant_id, UUID) else UUID(str(merchant_id))


def _commercial_values(product: SeedProduct) -> tuple[object, ...]:
    return (
        product.brand,
        product.model,
        product.category,
        product.title,
        product.description,
        product.offer_price_paise,
        product.mrp_paise,
        product.inventory_quantity,
        product.is_active,
        product.specifications,
        product.search_tags,
        product.image_url,
        product.source_url,
        product.specifications_verified_at,
    )


def _stored_values(row: dict[str, object]) -> tuple[object, ...]:
    return (
        row["brand"],
        row["model"],
        row["category"],
        row["title"],
        row["description"],
        row["offer_price_paise"],
        row["mrp_paise"],
        row["inventory_quantity"],
        row["is_active"],
        row["specifications"],
        row["search_tags"],
        row["image_url"],
        row["source_url"],
        row["specifications_verified_at"],
    )


def import_catalog(products: list[SeedProduct]) -> tuple[int, int, int]:
    settings = get_settings()
    engine = create_engine(settings.sqlalchemy_migration_url)
    inserted = updated = unchanged = 0
    try:
        with engine.begin() as connection:
            merchant_id = _ensure_merchant(connection)
            rows = connection.execute(
                text(
                    """
                    SELECT id, sku, brand, model, category, title, description,
                           offer_price_paise, mrp_paise, inventory_quantity, is_active,
                           specifications, search_tags, image_url, source_url,
                           specifications_verified_at
                    FROM products
                    WHERE merchant_id = :merchant_id
                    """
                ),
                {"merchant_id": merchant_id},
            ).mappings()
            existing = {str(row["sku"]): dict(row) for row in rows}

            for product in products:
                stored = existing.get(product.sku)
                parameters = product.parameters(merchant_id)
                if stored is None:
                    connection.execute(
                        text(
                            """
                            INSERT INTO products (
                                id, merchant_id, sku, brand, model, category, title,
                                description, offer_price_paise, mrp_paise,
                                inventory_quantity, is_active, specifications,
                                search_tags, image_url, source_url,
                                specifications_verified_at
                            ) VALUES (
                                :id, :merchant_id, :sku, :brand, :model,
                                CAST(:category AS product_category), :title,
                                :description, :offer_price_paise, :mrp_paise,
                                :inventory_quantity, :is_active,
                                CAST(:specifications AS jsonb), CAST(:search_tags AS jsonb),
                                :image_url, :source_url, :specifications_verified_at
                            )
                            """
                        ),
                        parameters,
                    )
                    inserted += 1
                elif _stored_values(stored) != _commercial_values(product):
                    parameters["product_id"] = stored["id"]
                    connection.execute(
                        text(
                            """
                            UPDATE products
                            SET brand = :brand,
                                model = :model,
                                category = CAST(:category AS product_category),
                                title = :title,
                                description = :description,
                                offer_price_paise = :offer_price_paise,
                                mrp_paise = :mrp_paise,
                                inventory_quantity = :inventory_quantity,
                                is_active = :is_active,
                                specifications = CAST(:specifications AS jsonb),
                                search_tags = CAST(:search_tags AS jsonb),
                                image_url = :image_url,
                                source_url = :source_url,
                                specifications_verified_at = :specifications_verified_at,
                                version = version + 1,
                                updated_at = now()
                            WHERE id = :product_id
                            """
                        ),
                        parameters,
                    )
                    updated += 1
                else:
                    unchanged += 1
    finally:
        engine.dispose()
    return inserted, updated, unchanged


def main() -> None:
    products = load_and_validate_catalog()
    inserted, updated, unchanged = import_catalog(products)
    print(
        "Catalogue import complete: "
        f"validated={len(products)}, inserted={inserted}, "
        f"updated={updated}, unchanged={unchanged}"
    )


if __name__ == "__main__":
    main()
