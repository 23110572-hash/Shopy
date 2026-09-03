"""Read-only public product catalogue queries."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.product import Product, ProductCategory


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_active(
        self,
        *,
        query: str | None,
        category: ProductCategory | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Product], int, dict[ProductCategory, int]]:
        filters: list[ColumnElement[bool]] = [Product.is_active.is_(True)]
        if category is not None:
            filters.append(Product.category == category)
        if query is not None and query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            filters.append(
                or_(
                    Product.title.ilike(pattern, escape="\\"),
                    Product.brand.ilike(pattern, escape="\\"),
                    Product.model.ilike(pattern, escape="\\"),
                    Product.description.ilike(pattern, escape="\\"),
                    Product.sku.ilike(pattern, escape="\\"),
                )
            )

        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Product).where(*filters)
                )
            ).scalar_one()
        )
        products = (
            (
                await self._session.execute(
                    select(Product)
                    .where(*filters)
                    .order_by(Product.category, Product.brand, Product.model, Product.sku)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        count_rows = (
            await self._session.execute(
                select(Product.category, func.count(Product.id))
                .where(Product.is_active.is_(True))
                .group_by(Product.category)
            )
        ).all()
        category_counts = {category_value: int(count) for category_value, count in count_rows}
        for category_value in ProductCategory:
            category_counts.setdefault(category_value, 0)
        return products, total, category_counts

    async def get_active(self, product_id: UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def search_agent_candidates(
        self,
        *,
        category: ProductCategory | None,
        allowed_categories: Sequence[ProductCategory] | None,
        max_price_paise: int | None,
        limit: int,
    ) -> Sequence[Product]:
        """Return only active, in-stock products that satisfy hard agent constraints."""
        filters: list[ColumnElement[bool]] = [
            Product.is_active.is_(True),
            Product.inventory_quantity > 0,
        ]
        if category is not None:
            filters.append(Product.category == category)
        elif allowed_categories:
            filters.append(Product.category.in_(allowed_categories))
        if max_price_paise is not None:
            filters.append(Product.offer_price_paise <= max_price_paise)

        result = await self._session.execute(
            select(Product)
            .where(*filters)
            .order_by(Product.offer_price_paise, Product.brand, Product.model, Product.sku)
            .limit(limit)
        )
        return result.scalars().all()

    async def get_for_checkout(self, product_id: UUID) -> Product | None:
        """Lock the current product row before quote or reservation validation."""
        result = await self._session.execute(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        return result.scalar_one_or_none()
