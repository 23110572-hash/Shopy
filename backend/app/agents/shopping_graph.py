"""Bounded catalogue retrieval and LLM product comparison orchestrated with LangGraph."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from backend.app.gateways.llm import LLMGateway
from backend.app.gateways.openrouter import LLMProviderError
from backend.app.models.product import ProductCategory
from backend.app.repositories.products import ProductRepository
from backend.app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentDecisionSource,
    AgentIntentSource,
    AgentProductDecision,
    AgentRecommendation,
    AgentRuntimeControls,
    ProductComparisonDecision,
    ShoppingIntent,
)
from backend.app.schemas.catalog import CatalogProduct

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_PRICE_PATTERN = re.compile(
    r"(?:under|below|less\s+than|within|up\s+to|max(?:imum)?(?:\s+of)?|budget(?:\s+of)?)"
    r"\s*(?:₹|rs\.?|inr)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lac)?",
    re.IGNORECASE,
)
_CATEGORY_ALIASES: dict[ProductCategory, frozenset[str]] = {
    ProductCategory.SMARTPHONES: frozenset(
        {"smartphone", "smartphones", "phone", "phones", "mobile"}
    ),
    ProductCategory.SPEAKERS: frozenset({"speaker", "speakers", "soundbar"}),
    ProductCategory.HEADPHONES: frozenset(
        {"headphone", "headphones", "earphone", "earphones", "earbud", "earbuds", "headset"}
    ),
    ProductCategory.LAPTOPS: frozenset({"laptop", "laptops", "notebook", "notebooks"}),
    ProductCategory.TABLETS: frozenset({"tablet", "tablets", "ipad"}),
}
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "best",
        "buy",
        "find",
        "for",
        "good",
        "i",
        "inr",
        "looking",
        "max",
        "maximum",
        "me",
        "need",
        "of",
        "please",
        "recommend",
        "recommendation",
        "recommendations",
        "rs",
        "rupees",
        "show",
        "some",
        "the",
        "to",
        "under",
        "up",
        "want",
        "with",
        "within",
    }
    | {alias for aliases in _CATEGORY_ALIASES.values() for alias in aliases}
)


class ShoppingGraphState(TypedDict, total=False):
    request: AgentChatRequest
    intent: ShoppingIntent
    intent_source: AgentIntentSource
    allowed_categories: list[ProductCategory]
    effective_limit: int
    blocked_reason: str
    controls_applied: bool
    candidates: list[CatalogProduct]
    shortlist: list[AgentRecommendation]
    complementary_candidates: list[AgentRecommendation]
    recommendations: list[AgentRecommendation]
    decision: AgentProductDecision
    response: AgentChatResponse


class ShoppingGraph:
    """Let the LLM compare real candidates while code keeps every hard boundary authoritative."""

    def __init__(
        self,
        repository: ProductRepository,
        llm_gateway: LLMGateway | None = None,
        controls: AgentRuntimeControls | None = None,
    ) -> None:
        self._repository = repository
        self._llm_gateway = llm_gateway
        self._controls = controls

        builder = StateGraph(ShoppingGraphState)
        builder.add_node("parse_intent", self._parse_intent)
        builder.add_node("apply_controls", self._apply_controls)
        builder.add_node("search_catalog", self._search_catalog)
        builder.add_node("rank_shortlist", self._rank_shortlist)
        builder.add_node("search_complements", self._search_complements)
        builder.add_node("compare_products", self._compare_products)
        builder.add_node("compose_response", self._compose_response)
        builder.add_edge(START, "parse_intent")
        builder.add_edge("parse_intent", "apply_controls")
        builder.add_edge("apply_controls", "search_catalog")
        builder.add_edge("search_catalog", "rank_shortlist")
        builder.add_edge("rank_shortlist", "search_complements")
        builder.add_edge("search_complements", "compare_products")
        builder.add_edge("compare_products", "compose_response")
        builder.add_edge("compose_response", END)
        self._graph = builder.compile()

    async def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        final_state = await self._graph.ainvoke(ShoppingGraphState(request=request))
        response = final_state.get("response")
        if response is None:
            raise RuntimeError("Shopping graph did not produce a response")
        return AgentChatResponse.model_validate(response)

    async def _parse_intent(self, state: ShoppingGraphState) -> ShoppingGraphState:
        request = state["request"]
        deterministic_intent = _parse_deterministic_intent(request)
        if self._llm_gateway is None:
            return ShoppingGraphState(
                intent=deterministic_intent,
                intent_source="deterministic",
            )

        try:
            raw_intent = await self._llm_gateway.parse_structured_intent(
                user_text=request.message,
                json_schema=ShoppingIntent.model_json_schema(),
            )
            provider_intent = ShoppingIntent.model_validate(raw_intent)
        except (LLMProviderError, ValidationError):
            return ShoppingGraphState(
                intent=deterministic_intent,
                intent_source="deterministic_fallback",
            )

        intent = ShoppingIntent(
            query=provider_intent.query or deterministic_intent.query,
            category=request.category or provider_intent.category or deterministic_intent.category,
            max_price_paise=(
                request.max_price_paise
                or provider_intent.max_price_paise
                or deterministic_intent.max_price_paise
            ),
            preferences=provider_intent.preferences or deterministic_intent.preferences,
        )
        return ShoppingGraphState(intent=intent, intent_source="openrouter")

    def _apply_controls(self, state: ShoppingGraphState) -> ShoppingGraphState:
        request = state["request"]
        controls = self._controls
        if controls is None:
            return ShoppingGraphState(
                effective_limit=request.limit,
                controls_applied=False,
            )
        if not controls.agent_enabled:
            return ShoppingGraphState(
                effective_limit=0,
                controls_applied=True,
                blocked_reason="Your Shopy Agent is disabled in Profile → Agent controls.",
            )

        intent = state["intent"]
        allowlist = controls.category_allowlist
        if intent.category is not None and allowlist and intent.category not in allowlist:
            return ShoppingGraphState(
                effective_limit=0,
                controls_applied=True,
                allowed_categories=allowlist,
                blocked_reason=(
                    f"{intent.category.value.title()} is outside your saved category allowlist. "
                    "Update it in Profile → Agent controls."
                ),
            )

        ceilings = [
            value
            for value in (
                intent.max_price_paise,
                controls.recommendation_price_ceiling_paise,
                controls.per_purchase_limit_paise,
            )
            if value is not None
        ]
        effective_ceiling = min(ceilings) if ceilings else None
        return ShoppingGraphState(
            intent=intent.model_copy(update={"max_price_paise": effective_ceiling}),
            allowed_categories=allowlist,
            effective_limit=min(request.limit, controls.max_recommendations),
            controls_applied=True,
        )

    async def _search_catalog(self, state: ShoppingGraphState) -> ShoppingGraphState:
        if state.get("blocked_reason"):
            return ShoppingGraphState(candidates=[])
        intent = state["intent"]
        products = await self._repository.search_agent_candidates(
            category=intent.category,
            allowed_categories=state.get("allowed_categories"),
            max_price_paise=intent.max_price_paise,
            limit=100,
        )
        return ShoppingGraphState(
            candidates=[CatalogProduct.model_validate(product) for product in products]
        )

    def _rank_shortlist(self, state: ShoppingGraphState) -> ShoppingGraphState:
        intent = state["intent"]
        preference_tokens = _preference_tokens(" ".join([intent.query, *intent.preferences]))
        ranked = [
            _score_candidate(product, intent, preference_tokens)
            for product in state.get("candidates", [])
        ]
        ranked.sort(
            key=lambda recommendation: (
                -recommendation.score,
                recommendation.product.offer_price_paise,
                recommendation.product.title.casefold(),
            )
        )
        shortlist = ranked[:8]
        return ShoppingGraphState(
            shortlist=shortlist,
            recommendations=shortlist[: state.get("effective_limit", state["request"].limit)],
        )

    async def _search_complements(self, state: ShoppingGraphState) -> ShoppingGraphState:
        shortlist = state.get("shortlist", [])
        if not shortlist:
            return ShoppingGraphState(complementary_candidates=[])
        primary_categories = {item.product.category for item in shortlist}
        configured_categories = state.get("allowed_categories") or list(ProductCategory)
        complementary_categories = [
            category for category in configured_categories if category not in primary_categories
        ]
        if not complementary_categories:
            return ShoppingGraphState(complementary_candidates=[])

        intent = state["intent"]
        products = await self._repository.search_agent_candidates(
            category=None,
            allowed_categories=complementary_categories,
            max_price_paise=intent.max_price_paise,
            limit=8,
        )
        complement_intent = intent.model_copy(update={"category": None})
        preference_tokens = _preference_tokens(
            " ".join([complement_intent.query, *complement_intent.preferences])
        )
        ranked = [
            _score_candidate(
                CatalogProduct.model_validate(product),
                complement_intent,
                preference_tokens,
            )
            for product in products
        ]
        ranked.sort(
            key=lambda recommendation: (
                -recommendation.score,
                recommendation.product.offer_price_paise,
                recommendation.product.title.casefold(),
            )
        )
        return ShoppingGraphState(complementary_candidates=ranked[:5])

    async def _compare_products(self, state: ShoppingGraphState) -> ShoppingGraphState:
        primary = state.get("shortlist", [])
        complementary = state.get("complementary_candidates", [])
        if not primary:
            return ShoppingGraphState()

        if self._llm_gateway is None:
            decision = _fallback_decision(primary, complementary, source="deterministic")
        else:
            candidate_payload = [
                _comparison_candidate(item.product, role="primary") for item in primary
            ] + [
                _comparison_candidate(item.product, role="complementary")
                for item in complementary
            ]
            try:
                raw_decision = await self._llm_gateway.compare_products(
                    user_text=state["request"].message,
                    intent=state["intent"].model_dump(mode="json"),
                    candidates=candidate_payload,
                    json_schema=ProductComparisonDecision.model_json_schema(),
                )
                provider_decision = ProductComparisonDecision.model_validate(raw_decision)
                decision = _validate_decision(
                    provider_decision,
                    primary,
                    complementary,
                    source="openrouter",
                )
            except (LLMProviderError, ValidationError, ValueError):
                decision = _fallback_decision(
                    primary,
                    complementary,
                    source="deterministic_fallback",
                )

        ordered = _ordered_primary_recommendations(primary, decision)
        effective_limit = state.get("effective_limit", state["request"].limit)
        return ShoppingGraphState(
            decision=decision,
            shortlist=ordered,
            recommendations=ordered[:effective_limit],
        )

    def _compose_response(self, state: ShoppingGraphState) -> ShoppingGraphState:
        recommendations = state.get("recommendations", [])
        primary = state.get("shortlist", [])
        complementary = state.get("complementary_candidates", [])
        intent = state["intent"]
        intent_source = state["intent_source"]
        decision = state.get("decision")
        controls_applied = state.get("controls_applied", False)

        parser_notice = _parser_notice(intent_source, decision)
        if controls_applied:
            parser_notice += " Saved account limits were enforced before comparison."

        winner: AgentRecommendation | None = None
        upsell: AgentRecommendation | None = None
        cross_sell: AgentRecommendation | None = None
        if decision is not None:
            primary_by_id = {item.product.id: item for item in primary}
            complementary_by_id = {item.product.id: item for item in complementary}
            winner = _with_decision_reason(
                primary_by_id[decision.selected_product_id], decision.winner_reason
            )
            if decision.upsell_product_id is not None:
                upsell = _with_decision_reason(
                    primary_by_id[decision.upsell_product_id],
                    decision.upsell_reason or "A higher-fit eligible alternative",
                )
            if decision.cross_sell_product_id is not None:
                cross_sell = _with_decision_reason(
                    complementary_by_id[decision.cross_sell_product_id],
                    decision.cross_sell_reason or "A complementary eligible product",
                )

        blocked_reason = state.get("blocked_reason")
        if blocked_reason:
            reply = blocked_reason
        elif winner is not None:
            reply = (
                f"I compared {len(primary)} real in-stock products and selected "
                f"{winner.product.title}. {decision.winner_reason if decision else ''}"
            ).strip()
        else:
            reply = (
                "I could not find an active, in-stock catalogue product that satisfies those "
                "filters and your saved controls. Try another category, feature, or price ceiling."
            )

        return ShoppingGraphState(
            response=AgentChatResponse(
                reply=reply,
                intent_source=intent_source,
                decision_source=decision.decision_source if decision else None,
                parser_notice=parser_notice,
                intent=intent,
                recommendations=recommendations,
                winner=winner,
                decision=decision,
                upsell=upsell,
                cross_sell=cross_sell,
                account_controls_applied=controls_applied,
                notice=(
                    "The winner is catalogue-backed and deterministically validated. Sign in to "
                    "create a short-lived quote; no Razorpay Order was created by this search."
                    if winner is not None
                    else "No purchase or payment was attempted."
                ),
            )
        )


def _comparison_candidate(product: CatalogProduct, *, role: str) -> dict[str, object]:
    return {
        "id": str(product.id),
        "role": role,
        "sku": product.sku,
        "brand": product.brand,
        "model": product.model,
        "category": product.category.value,
        "title": product.title,
        "description": product.description,
        "offer_price_paise": product.offer_price_paise,
        "mrp_paise": product.mrp_paise,
        "inventory_quantity": product.inventory_quantity,
        "specifications": product.specifications,
        "search_tags": product.search_tags,
        "specifications_verified_at": product.specifications_verified_at.isoformat(),
        "version": product.version,
    }


def _validate_decision(
    decision: ProductComparisonDecision,
    primary: list[AgentRecommendation],
    complementary: list[AgentRecommendation],
    *,
    source: AgentDecisionSource,
) -> AgentProductDecision:
    primary_ids = {item.product.id for item in primary}
    complementary_ids = {item.product.id for item in complementary}
    if decision.selected_product_id not in primary_ids:
        raise ValueError("The model selected an unknown or non-primary product")

    ranked_ids: list[UUID] = [decision.selected_product_id]
    for product_id in decision.ranked_product_ids:
        if product_id in primary_ids and product_id not in ranked_ids:
            ranked_ids.append(product_id)
    for recommendation in primary:
        if recommendation.product.id not in ranked_ids:
            ranked_ids.append(recommendation.product.id)

    upsell_id = decision.upsell_product_id
    if upsell_id not in primary_ids or upsell_id == decision.selected_product_id:
        upsell_id = None
    cross_sell_id = decision.cross_sell_product_id
    if cross_sell_id not in complementary_ids:
        cross_sell_id = None

    return AgentProductDecision(
        selected_product_id=decision.selected_product_id,
        ranked_product_ids=ranked_ids[:8],
        winner_reason=decision.winner_reason,
        tradeoffs=decision.tradeoffs,
        upsell_product_id=upsell_id,
        upsell_reason=decision.upsell_reason if upsell_id is not None else None,
        cross_sell_product_id=cross_sell_id,
        cross_sell_reason=decision.cross_sell_reason if cross_sell_id is not None else None,
        decision_source=source,
    )


def _fallback_decision(
    primary: list[AgentRecommendation],
    complementary: list[AgentRecommendation],
    *,
    source: AgentDecisionSource,
) -> AgentProductDecision:
    winner = primary[0]
    upsell = next(
        (
            item
            for item in primary[1:]
            if item.product.offer_price_paise > winner.product.offer_price_paise
        ),
        primary[1] if len(primary) > 1 else None,
    )
    cross_sell = complementary[0] if complementary else None
    return AgentProductDecision(
        selected_product_id=winner.product.id,
        ranked_product_ids=[item.product.id for item in primary],
        winner_reason=(
            "Highest transparent fallback score after hard catalogue and account-policy filters."
        ),
        tradeoffs=["The provider comparison was unavailable, so no model claim was invented."],
        upsell_product_id=upsell.product.id if upsell else None,
        upsell_reason="Next eligible higher-fit alternative from the same bounded shortlist."
        if upsell
        else None,
        cross_sell_product_id=cross_sell.product.id if cross_sell else None,
        cross_sell_reason="Top eligible complementary catalogue result."
        if cross_sell
        else None,
        decision_source=source,
    )


def _ordered_primary_recommendations(
    primary: list[AgentRecommendation],
    decision: AgentProductDecision,
) -> list[AgentRecommendation]:
    by_id = {item.product.id: item for item in primary}
    return [by_id[product_id] for product_id in decision.ranked_product_ids]


def _with_decision_reason(
    recommendation: AgentRecommendation,
    reason: str,
) -> AgentRecommendation:
    reasons = [reason, *recommendation.reasons]
    return recommendation.model_copy(update={"reasons": reasons[:6]})


def _parser_notice(
    intent_source: AgentIntentSource,
    decision: AgentProductDecision | None,
) -> str:
    intent_notice = {
        "deterministic": "Intent used transparent local parsing.",
        "openrouter": "The configured OpenRouter model parsed the shopping intent.",
        "deterministic_fallback": "Intent parsing fell back safely to transparent local rules.",
    }[intent_source]
    if decision is None:
        return f"{intent_notice} No eligible candidate reached product comparison."
    decision_notice = {
        "openrouter": (
            "The configured OpenRouter model compared real candidate specifications and selected "
            "one ID; code validated that ID against the supplied set."
        ),
        "deterministic": "No model was configured, so final selection used transparent scoring.",
        "deterministic_fallback": (
            "Model comparison failed safely, so final selection used transparent scoring."
        ),
    }[decision.decision_source]
    return f"{intent_notice} {decision_notice}"


def _parse_deterministic_intent(request: AgentChatRequest) -> ShoppingIntent:
    normalized = request.message.casefold()
    detected_category = request.category or _detect_category(normalized)
    max_price_paise = request.max_price_paise or _extract_max_price_paise(normalized)
    preferences = _preference_tokens(normalized)
    return ShoppingIntent(
        query=" ".join(preferences),
        category=detected_category,
        max_price_paise=max_price_paise,
        preferences=preferences,
    )


def _detect_category(text: str) -> ProductCategory | None:
    tokens = set(_TOKEN_PATTERN.findall(text))
    for category, aliases in _CATEGORY_ALIASES.items():
        if tokens & aliases:
            return category
    return None


def _extract_max_price_paise(text: str) -> int | None:
    match = _PRICE_PATTERN.search(text)
    if match is None:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    suffix = (match.group(2) or "").casefold()
    multiplier = {
        "": Decimal(1),
        "k": Decimal(1_000),
        "thousand": Decimal(1_000),
        "lakh": Decimal(100_000),
        "lac": Decimal(100_000),
    }[suffix]
    paise = int(amount * multiplier * 100)
    return paise if 0 < paise <= 1_000_000_000 else None


def _preference_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_PATTERN.findall(text.casefold()):
        if token in _STOP_WORDS or token.isdigit() or len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:12]


def _score_candidate(
    product: CatalogProduct,
    intent: ShoppingIntent,
    preference_tokens: list[str],
) -> AgentRecommendation:
    score = 30
    reasons: list[str] = []

    if intent.category is not None and product.category == intent.category:
        score += 20
        reasons.append(f"Matches the {intent.category.value} category")

    title_tokens = set(
        _TOKEN_PATTERN.findall(
            f"{product.brand} {product.model} {product.title} {product.description}".casefold()
        )
    )
    tag_tokens = set(_TOKEN_PATTERN.findall(" ".join(product.search_tags).casefold()))
    matched = [token for token in preference_tokens if token in title_tokens or token in tag_tokens]
    if matched:
        score += min(30, len(matched) * 8)
        reasons.append(f"Matches: {', '.join(matched[:3])}")

    if intent.max_price_paise is not None:
        score += 10
        reasons.append(f"Within the {_format_inr(intent.max_price_paise)} ceiling")

    if product.mrp_paise is not None and product.mrp_paise > product.offer_price_paise:
        discount = round((1 - product.offer_price_paise / product.mrp_paise) * 100)
        score += min(7, max(1, discount // 5))
        reasons.append(f"{discount}% below listed MRP")

    score += min(3, product.inventory_quantity)
    reasons.append(f"In stock: {product.inventory_quantity} available")
    if not matched and intent.category is None:
        reasons.insert(0, "Eligible active catalogue match")

    return AgentRecommendation(
        product=product,
        score=min(score, 100),
        reasons=reasons[:6],
    )


def _format_inr(paise: int) -> str:
    return f"₹{paise // 100:,}"
