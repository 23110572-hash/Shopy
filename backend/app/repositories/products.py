"""Full-catalogue search and authoritative product reads."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, and_, case, desc, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import CatalogCategory, CatalogCategoryRelation, Product

_SEARCH_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CatalogCategoryDescriptor:
    slug: str
    display_name: str
    description: str
    aliases: list[str]
    facet_definitions: list[dict[str, object]]
    active_product_count: int


@dataclass(frozen=True, slots=True)
class PostPurchaseCrossSellCandidate:
    product: Product
    benefit: str
    relation_type: str


@dataclass(frozen=True, slots=True)
class AgentCatalogHit:
    product: Product
    relevance: float
    matched_terms: list[str]


@dataclass(frozen=True, slots=True)
class AgentCatalogDiagnostics:
    total_in_stock: int
    category_matches: int
    text_matches: int
    eligible_matches: int
    lowest_matching_price_paise: int | None
    reason: str
    applied_categories: list[str]
    applied_query: str


@dataclass(frozen=True, slots=True)
class AgentCatalogResult:
    hits: list[AgentCatalogHit]
    diagnostics: AgentCatalogDiagnostics


def _search_terms(value: str) -> list[str]:
    terms: list[str] = []
    for token in _SEARCH_TOKEN.findall(value.casefold()):
        if len(token) < 2 or token in terms:
            continue
        terms.append(token)
    return terms[:20]


def _exclusion_terms(values: Sequence[str] | None) -> list[str]:
    generic = {
        "brand",
        "brands",
        "model",
        "models",
        "phone",
        "phones",
        "product",
        "products",
        "smartphone",
        "smartphones",
    }
    result: list[str] = []
    for value in values or []:
        tokens = [token for token in _search_terms(value) if token not in generic]
        term = " ".join(tokens).strip()
        if term and term not in result:
            result.append(term[:120])
    return result[:16]


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_active(
        self,
        *,
        query: str | None,
        category: str | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Product], int, dict[str, int]]:
        filters: list[ColumnElement[bool]] = [Product.is_active.is_(True)]
        if category is not None:
            filters.append(Product.category == category)
        if query is not None and query.strip():
            terms = _search_terms(query)
            if terms:
                filters.append(self._text_match(terms, query))

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
        return products, total, {str(category_value): int(count) for category_value, count in count_rows}

    async def list_catalogue_categories(self) -> list[CatalogCategoryDescriptor]:
        rows = (
            await self._session.execute(
                select(CatalogCategory, func.count(Product.id))
                .outerjoin(
                    Product,
                    and_(
                        Product.category == CatalogCategory.slug,
                        Product.is_active.is_(True),
                    ),
                )
                .where(CatalogCategory.is_active.is_(True))
                .group_by(CatalogCategory.slug)
                .order_by(CatalogCategory.sort_order, CatalogCategory.display_name)
            )
        ).all()
        return [
            CatalogCategoryDescriptor(
                slug=category.slug,
                display_name=category.display_name,
                description=category.description,
                aliases=[str(value) for value in category.aliases],
                facet_definitions=[dict(value) for value in category.facet_definitions],
                active_product_count=int(count),
            )
            for category, count in rows
        ]

    async def get_active(self, product_id: UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(Product.id == product_id, Product.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_active_many(self, product_ids: Sequence[UUID]) -> Sequence[Product]:
        if not product_ids:
            return []
        result = await self._session.execute(
            select(Product).where(
                Product.id.in_(product_ids),
                Product.is_active.is_(True),
            )
        )
        by_id = {product.id: product for product in result.scalars().all()}
        return [by_id[product_id] for product_id in product_ids if product_id in by_id]

    async def list_agent_identity_catalog(self) -> Sequence[Product]:
        """Return the bounded catalogue used for exact identity and family resolution."""

        result = await self._session.execute(
            select(Product).order_by(Product.brand, Product.model, Product.id)
        )
        return result.scalars().all()

    async def find_post_purchase_cross_sell(
        self,
        *,
        source_product_id: UUID,
        source_merchant_id: UUID,
        source_category: str,
        source_brand: str,
        allowed_categories: Sequence[str] | None,
        max_price_paise: int | None,
    ) -> PostPurchaseCrossSellCandidate | None:
        filters: list[ColumnElement[bool]] = [
            CatalogCategoryRelation.source_category == source_category,
            CatalogCategoryRelation.relation_type == "POST_PURCHASE_CROSS_SELL",
            CatalogCategoryRelation.is_active.is_(True),
            Product.merchant_id == source_merchant_id,
            Product.id != source_product_id,
            Product.is_active.is_(True),
            Product.inventory_quantity > 0,
        ]
        if allowed_categories:
            filters.append(Product.category.in_(allowed_categories))
        if max_price_paise is not None:
            filters.append(Product.offer_price_paise <= max_price_paise)

        row = (
            await self._session.execute(
                select(
                    Product,
                    CatalogCategoryRelation.benefit,
                    CatalogCategoryRelation.relation_type,
                )
                .join(
                    CatalogCategoryRelation,
                    CatalogCategoryRelation.target_category == Product.category,
                )
                .where(*filters)
                .order_by(
                    CatalogCategoryRelation.sort_order,
                    case(
                        (func.lower(Product.brand) == source_brand.casefold(), 0),
                        else_=1,
                    ),
                    Product.offer_price_paise,
                    Product.brand,
                    Product.model,
                    Product.id,
                )
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        product, benefit, relation_type = row
        return PostPurchaseCrossSellCandidate(
            product=product,
            benefit=str(benefit),
            relation_type=str(relation_type),
        )

    async def search_agent_catalog(
        self,
        *,
        query: str,
        category_slugs: Sequence[str],
        allowed_categories: Sequence[str] | None,
        max_price_paise: int | None,
        limit: int,
        exclude_product_ids: Sequence[UUID] | None = None,
        required_brand: str | None = None,
        excluded_terms: Sequence[str] | None = None,
    ) -> AgentCatalogResult:
        """Rank across the complete eligible corpus, then apply the response limit."""

        normalized_query = " ".join(query.split()).casefold()[:240]
        terms = _search_terms(normalized_query)
        requested_categories = list(dict.fromkeys(category_slugs))
        allowed = set(allowed_categories or [])
        if requested_categories and allowed:
            effective_categories = [value for value in requested_categories if value in allowed]
        elif requested_categories:
            effective_categories = requested_categories
        else:
            effective_categories = list(allowed)

        base_filters: list[ColumnElement[bool]] = [
            Product.is_active.is_(True),
            Product.inventory_quantity > 0,
        ]
        total_in_stock = await self._count(base_filters)

        category_filters = list(base_filters)
        impossible_category = bool(requested_categories and allowed and not effective_categories)
        if impossible_category:
            category_filters.append(false())
        elif effective_categories:
            category_filters.append(Product.category.in_(effective_categories))
        category_matches = await self._count(category_filters)

        text_predicate = self._text_match(terms, normalized_query) if terms else None
        retrieval_filters = list(category_filters)
        if required_brand is not None and required_brand.strip():
            retrieval_filters.append(
                func.lower(Product.brand) == required_brand.strip().casefold()
            )
        # A trusted category narrows the search space; text then ranks the full category
        # instead of accidentally hiding relevant items with sparse catalogue copy.
        if text_predicate is not None and not effective_categories:
            retrieval_filters.append(text_predicate)
        text_matches = await self._count(
            [*retrieval_filters, text_predicate]
            if text_predicate is not None and effective_categories
            else retrieval_filters
        )

        lowest_price = (
            await self._session.execute(
                select(func.min(Product.offer_price_paise)).where(*retrieval_filters)
            )
        ).scalar_one()

        eligible_filters = list(retrieval_filters)
        if max_price_paise is not None:
            eligible_filters.append(Product.offer_price_paise <= max_price_paise)
        if exclude_product_ids:
            eligible_filters.append(Product.id.not_in(exclude_product_ids))
        for excluded_term in _exclusion_terms(excluded_terms):
            pattern = f"%{excluded_term.casefold()}%"
            eligible_filters.append(
                ~or_(
                    func.lower(Product.brand).like(pattern),
                    func.lower(Product.model).like(pattern),
                    func.lower(Product.title).like(pattern),
                )
            )
        eligible_matches = await self._count(eligible_filters)

        if terms:
            ts_query = func.websearch_to_tsquery("simple", " OR ".join(terms))
            vector = func.to_tsvector("simple", func.coalesce(Product.search_document, ""))
            rank = func.ts_rank_cd(vector, ts_query)
            similarity = func.similarity(
                func.coalesce(Product.search_document, ""),
                normalized_query,
            )
            exact_bonus = case(
                (func.lower(Product.search_document).contains(normalized_query), 1.0),
                else_=0.0,
            )
            relevance = (rank * 4.0 + similarity + exact_bonus).label("catalogue_relevance")
        else:
            relevance = case((Product.id.is_not(None), 0.1), else_=0.0).label(
                "catalogue_relevance"
            )

        rows = (
            await self._session.execute(
                select(Product, relevance)
                .where(*eligible_filters)
                .order_by(desc(relevance), Product.brand, Product.model, Product.id)
                .limit(max(1, min(limit, 50)))
            )
        ).all()
        hits = [
            AgentCatalogHit(
                product=product,
                relevance=float(score or 0),
                matched_terms=[term for term in terms if term in product.search_document.casefold()][
                    :8
                ],
            )
            for product, score in rows
        ]

        if eligible_matches:
            reason = "MATCHES_FOUND"
        elif category_matches == 0:
            reason = "NO_CATEGORY_MATCH"
        elif text_predicate is not None and not effective_categories and text_matches == 0:
            reason = "NO_TEXT_MATCH"
        elif (
            max_price_paise is not None
            and lowest_price is not None
            and int(lowest_price) > max_price_paise
        ):
            reason = "OVER_BUDGET"
        else:
            reason = "NO_ELIGIBLE_PRODUCT"
        diagnostics = AgentCatalogDiagnostics(
            total_in_stock=total_in_stock,
            category_matches=category_matches,
            text_matches=text_matches,
            eligible_matches=eligible_matches,
            lowest_matching_price_paise=(int(lowest_price) if lowest_price is not None else None),
            reason=reason,
            applied_categories=effective_categories,
            applied_query=normalized_query,
        )
        return AgentCatalogResult(hits=hits, diagnostics=diagnostics)

    async def search_agent_candidates(
        self,
        *,
        category: str | None,
        allowed_categories: Sequence[str] | None,
        max_price_paise: int | None,
        limit: int,
        exclude_product_ids: Sequence[UUID] | None = None,
    ) -> Sequence[Product]:
        """Compatibility wrapper for callers migrating to the search-plan API."""
        result = await self.search_agent_catalog(
            query="",
            category_slugs=[category] if category else [],
            allowed_categories=allowed_categories,
            max_price_paise=max_price_paise,
            limit=limit,
            exclude_product_ids=exclude_product_ids,
        )
        return [hit.product for hit in result.hits]

    async def get_for_checkout(self, product_id: UUID) -> Product | None:
        result = await self._session.execute(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _count(self, filters: Sequence[ColumnElement[bool]]) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Product).where(*filters)
                )
            ).scalar_one()
        )

    @staticmethod
    def _text_match(terms: Sequence[str], raw_query: str) -> ColumnElement[bool]:
        if not terms:
            return Product.id.is_not(None)
        vector = func.to_tsvector("simple", func.coalesce(Product.search_document, ""))
        ts_query = func.websearch_to_tsquery("simple", " OR ".join(terms))
        return or_(
            vector.op("@@")(ts_query),
            func.similarity(func.coalesce(Product.search_document, ""), raw_query) > 0.08,
        )
