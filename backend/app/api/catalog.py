"""Public merchant catalogue API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database import Database
from app.dependencies import get_database
from app.models.product import ProductCategory
from app.repositories.products import ProductRepository
from app.schemas.catalog import CatalogPage, CatalogProduct

router = APIRouter(prefix="/api/catalog", tags=["catalog"])
product_router = APIRouter(prefix="/api/products", tags=["catalog"])
DatabaseDependency = Annotated[Database, Depends(get_database)]


async def _search(
    database: Database,
    query: str | None,
    category: ProductCategory | None,
    limit: int,
    offset: int,
) -> CatalogPage:
    async with database.session() as session:
        products, total, category_counts = await ProductRepository(session).search_active(
            query=query,
            category=category,
            limit=limit,
            offset=offset,
        )
        return CatalogPage(
            items=[CatalogProduct.model_validate(product) for product in products],
            total=total,
            limit=limit,
            offset=offset,
            category_counts=category_counts,
        )


@router.get("", response_model=CatalogPage)
async def list_catalog(
    database: DatabaseDependency,
    q: Annotated[str | None, Query(max_length=120)] = None,
    category: ProductCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CatalogPage:
    return await _search(database, q, category, limit, offset)


@router.get("/search", response_model=CatalogPage)
async def search_catalog(
    database: DatabaseDependency,
    q: Annotated[str | None, Query(max_length=120)] = None,
    category: ProductCategory | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CatalogPage:
    return await _search(database, q, category, limit, offset)


@product_router.get("/{product_id}", response_model=CatalogProduct)
async def get_product(product_id: UUID, database: DatabaseDependency) -> CatalogProduct:
    async with database.session() as session:
        product = await ProductRepository(session).get_active(product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        return CatalogProduct.model_validate(product)
