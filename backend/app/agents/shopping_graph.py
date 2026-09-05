"""LLM-first catalogue research and grounded product selection with LangGraph."""

from __future__ import annotations

from typing import Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.gateways.llm import LLMGateway
from app.gateways.openrouter import LLMProviderError
from app.repositories.products import (
    AgentCatalogDiagnostics,
    AgentCatalogHit,
    AgentCatalogResult,
    CatalogCategoryDescriptor,
    ProductRepository,
)
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentClarification,
    AgentDecisionSource,
    AgentIntentMode,
    AgentIntentSource,
    AgentProductDecision,
    AgentRecommendation,
    AgentRuntimeControls,
    ClarificationOption,
    ProductComparisonDecision,
    RetrievalEvaluation,
    ShoppingIntent,
    ShoppingUnderstanding,
)
from app.schemas.catalog import CatalogProduct, CatalogSearchDiagnostics

MAX_RETRIEVAL_PASSES = 3
MAX_RETRIEVAL_CANDIDATES = 40
MAX_FINALISTS = 8


class ShoppingGraphState(TypedDict, total=False):
    request: AgentChatRequest
    taxonomy: list[CatalogCategoryDescriptor]
    understanding: ShoppingUnderstanding
    intent: ShoppingIntent
    intent_mode: AgentIntentMode
    intent_source: AgentIntentSource
    category_slugs: list[str]
    search_query: str
    soft_preferences: list[str]
    hard_requirements: list[str]
    allowed_categories: list[str]
    forced_product_id: UUID
    excluded_product_ids: list[UUID]
    exact_match: bool
    effective_limit: int
    controls_applied: bool
    blocked_reason: str
    clarification: AgentClarification
    retrieval_passes: int
    seen_plan_keys: list[str]
    search_result: AgentCatalogResult
    accumulated_hits: list[AgentCatalogHit]
    evaluation: RetrievalEvaluation
    shortlist: list[AgentRecommendation]
    recommendations: list[AgentRecommendation]
    decision: AgentProductDecision
    response: AgentChatResponse


class ShoppingGraph:
    """Let the LLM understand, research and decide; code preserves catalogue truth."""

    def __init__(
        self,
        repository: ProductRepository,
        llm_gateway: LLMGateway | None = None,
        controls: AgentRuntimeControls | None = None,
        *,
        conversation_context: dict[str, object] | None = None,
        reference_products: list[CatalogProduct] | None = None,
        cross_sell_allowed: bool = False,
    ) -> None:
        self._repository = repository
        self._llm_gateway = llm_gateway
        self._controls = controls
        self._conversation_context = conversation_context or {}
        self._reference_products = reference_products or []
        # Cross-sells remain a separate, explicit product journey. This graph never
        # silently bundles them into the selected primary purchase.
        self._cross_sell_allowed = cross_sell_allowed

        builder = StateGraph(ShoppingGraphState)
        builder.add_node("load_catalogue_context", self._load_catalogue_context)
        builder.add_node("understand_request", self._understand_request)
        builder.add_node("apply_controls", self._apply_controls)
        builder.add_node("retrieve_catalogue", self._retrieve_catalogue)
        builder.add_node("evaluate_results", self._evaluate_results)
        builder.add_node("compare_products", self._compare_products)
        builder.add_node("compose_response", self._compose_response)
        builder.add_edge(START, "load_catalogue_context")
        builder.add_edge("load_catalogue_context", "understand_request")
        builder.add_edge("understand_request", "apply_controls")
        builder.add_conditional_edges(
            "apply_controls",
            self._route_after_controls,
            {"retrieve": "retrieve_catalogue", "compose": "compose_response"},
        )
        builder.add_edge("retrieve_catalogue", "evaluate_results")
        builder.add_conditional_edges(
            "evaluate_results",
            self._route_after_evaluation,
            {
                "retrieve": "retrieve_catalogue",
                "compare": "compare_products",
                "compose": "compose_response",
            },
        )
        builder.add_edge("compare_products", "compose_response")
        builder.add_edge("compose_response", END)
        self._graph = builder.compile()

    async def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        final_state = await self._graph.ainvoke(ShoppingGraphState(request=request))
        response = final_state.get("response")
        if response is None:
            raise RuntimeError("Shopping graph did not produce a response")
        return AgentChatResponse.model_validate(response)

    async def _load_catalogue_context(
        self, state: ShoppingGraphState
    ) -> ShoppingGraphState:
        taxonomy = await self._repository.list_catalogue_categories()
        return ShoppingGraphState(
            taxonomy=taxonomy,
            retrieval_passes=0,
            seen_plan_keys=[],
            accumulated_hits=[],
            effective_limit=state["request"].limit,
            controls_applied=False,
        )

    async def _understand_request(
        self, state: ShoppingGraphState
    ) -> ShoppingGraphState:
        request = state["request"]
        previous_intent = _previous_intent(self._conversation_context)
        default_intent = ShoppingIntent(
            query=previous_intent.query if previous_intent else "",
            category=request.category or (previous_intent.category if previous_intent else None),
            max_price_paise=request.max_price_paise
            or (previous_intent.max_price_paise if previous_intent else None),
            preferences=previous_intent.preferences if previous_intent else [],
        )
        if self._llm_gateway is None:
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="RECOMMEND",
                intent_source="deterministic_fallback",
                blocked_reason=(
                    "The Agent's reasoning service is temporarily unavailable. "
                    "No recommendation, order, or payment action was created."
                ),
            )

        allowed_reference_ids = {product.id for product in self._reference_products}
        catalogue_payload = {
            "categories": [
                {
                    "slug": category.slug,
                    "name": category.display_name,
                    "description": category.description,
                    "aliases": category.aliases,
                    "facets": category.facet_definitions,
                    "active_product_count": category.active_product_count,
                }
                for category in state.get("taxonomy", [])
            ],
            "money_unit": "INR",
            "searchable_product_fields": [
                "sku",
                "brand",
                "model",
                "title",
                "description",
                "category",
                "search_tags",
                "specifications",
            ],
        }
        conversation_payload = {
            "previous_intent": (
                previous_intent.model_dump(mode="json") if previous_intent else None
            ),
            "previous_intent_mode": self._conversation_context.get("intent_mode"),
            "profile_display_name": self._conversation_context.get(
                "profile_display_name"
            ),
            "recent_turns": self._conversation_context.get("recent_turns", []),
            "allowed_previous_products": [
                _compact_product(product) for product in self._reference_products
            ],
            "current_focus_product_id": self._conversation_context.get(
                "focus_product_id"
            ),
            "client_selected_product_id": (
                str(request.selected_product_id)
                if request.selected_product_id is not None
                else None
            ),
        }
        try:
            raw = await self._llm_gateway.understand_request(
                user_text=request.message,
                conversation_context=conversation_payload,
                catalogue_context=catalogue_payload,
                json_schema=ShoppingUnderstanding.model_json_schema(),
            )
            understanding = ShoppingUnderstanding.model_validate(raw)
        except (LLMProviderError, ValidationError):
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="RECOMMEND",
                intent_source="deterministic_fallback",
                blocked_reason=(
                    "The Agent could not safely understand this request right now. "
                    "No recommendation, order, or payment action was created. Please try again."
                ),
            )

        known_categories = {category.slug for category in state.get("taxonomy", [])}
        unknown_categories = set(understanding.category_slugs) - known_categories
        returned_reference_ids = set(understanding.referenced_product_ids)
        returned_exclusion_ids = set(understanding.excluded_product_ids)
        if unknown_categories:
            return ShoppingGraphState(
                understanding=understanding,
                intent=default_intent,
                intent_mode="RECOMMEND",
                intent_source="openrouter",
                blocked_reason=(
                    "The Agent produced a catalogue category that does not exist. "
                    "No product or payment action was selected. Please try again."
                ),
            )
        if not returned_reference_ids.issubset(allowed_reference_ids) or not (
            returned_exclusion_ids.issubset(allowed_reference_ids)
        ):
            return ShoppingGraphState(
                understanding=understanding,
                intent=default_intent,
                intent_mode="RECOMMEND",
                intent_source="openrouter",
                clarification=_reference_clarification(
                    "I could not safely connect that reference to one of the products shown in this session.",
                    self._reference_products,
                ),
            )

        selected_product_id = request.selected_product_id
        if selected_product_id is not None and selected_product_id not in allowed_reference_ids:
            return ShoppingGraphState(
                understanding=understanding,
                intent=default_intent,
                intent_mode="RECOMMEND",
                intent_source="openrouter",
                clarification=_reference_clarification(
                    "That product is not one of the verified options in this session. Which product did you mean?",
                    self._reference_products,
                ),
            )

        forced_product_id: UUID | None = selected_product_id
        if forced_product_id is None and understanding.reference_status == "RESOLVED":
            forced_product_id = understanding.referenced_product_ids[0]

        if understanding.needs_clarification or understanding.reference_status in {
            "AMBIGUOUS",
            "INVALID",
        }:
            question = understanding.clarification_question or (
                "Which of the products from the current session did you mean?"
            )
            clarification = (
                _reference_clarification(question, self._reference_products)
                if understanding.reference_status in {"AMBIGUOUS", "INVALID"}
                else AgentClarification(
                    kind="REQUIREMENTS", question=question, options=[]
                )
            )
        else:
            clarification = None

        provider_ceiling = (
            understanding.budget.maximum_inr * 100
            if understanding.budget.maximum_inr is not None
            else None
        )
        continuing = (
            understanding.intent_mode == "REFINE" or forced_product_id is not None
        )
        max_price_paise = (
            request.max_price_paise
            or provider_ceiling
            or (
                previous_intent.max_price_paise
                if continuing and previous_intent is not None
                else None
            )
        )
        categories = list(understanding.category_slugs)
        if request.category is not None:
            categories = [request.category]
        elif not categories and continuing and previous_intent and previous_intent.category:
            categories = [previous_intent.category]

        search_query = understanding.search_query
        if not search_query and continuing and previous_intent is not None:
            search_query = previous_intent.query
        preferences = list(
            dict.fromkeys(
                [*understanding.hard_requirements, *understanding.soft_preferences]
            )
        )[:16]
        if not preferences and continuing and previous_intent is not None:
            preferences = previous_intent.preferences

        previous_mode = self._conversation_context.get("intent_mode")
        intent_mode = understanding.intent_mode
        if intent_mode == "REFINE":
            intent_mode = (
                previous_mode
                if previous_mode in {"BUY", "RECOMMEND", "COMPARE"}
                else "RECOMMEND"
            )
        elif selected_product_id is not None and previous_mode == "BUY":
            # The product identity is explicit typed state from a prior BUY clarification.
            intent_mode = "BUY"

        intent = ShoppingIntent(
            query=search_query,
            category=categories[0] if categories else None,
            max_price_paise=max_price_paise,
            preferences=preferences,
        )
        result = ShoppingGraphState(
            understanding=understanding,
            intent=intent,
            intent_mode=intent_mode,
            intent_source="openrouter",
            category_slugs=categories,
            search_query=search_query,
            soft_preferences=list(understanding.soft_preferences),
            hard_requirements=list(understanding.hard_requirements),
            excluded_product_ids=list(understanding.excluded_product_ids),
            exact_match=forced_product_id is not None,
        )
        if forced_product_id is not None:
            result["forced_product_id"] = forced_product_id
        if clarification is not None:
            result["clarification"] = clarification
        return result

    def _apply_controls(self, state: ShoppingGraphState) -> ShoppingGraphState:
        if state.get("blocked_reason") or state.get("clarification"):
            return ShoppingGraphState()
        if state.get("intent_mode") == "OTHER":
            return ShoppingGraphState()
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

        categories = state.get("category_slugs", [])
        allowlist = controls.category_allowlist
        if categories and allowlist and not set(categories).intersection(allowlist):
            return ShoppingGraphState(
                effective_limit=0,
                controls_applied=True,
                allowed_categories=allowlist,
                blocked_reason=(
                    f"{', '.join(categories)} is outside your saved category allowlist. "
                    "Update it in Profile → Agent controls."
                ),
            )

        intent = state["intent"]
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

    def _route_after_controls(
        self, state: ShoppingGraphState
    ) -> Literal["retrieve", "compose"]:
        if (
            state.get("blocked_reason")
            or state.get("clarification")
            or state.get("intent_mode") == "OTHER"
        ):
            return "compose"
        return "retrieve"

    async def _retrieve_catalogue(
        self, state: ShoppingGraphState
    ) -> ShoppingGraphState:
        forced_product_id = state.get("forced_product_id")
        if forced_product_id is not None:
            product = await self._repository.get_active(forced_product_id)
            intent = state["intent"]
            allowed = state.get("allowed_categories", [])
            eligible = (
                product is not None
                and product.in_stock
                and (not allowed or product.category in allowed)
                and (
                    intent.max_price_paise is None
                    or product.offer_price_paise <= intent.max_price_paise
                )
                and product.id not in state.get("excluded_product_ids", [])
            )
            hits = (
                [AgentCatalogHit(product=product, relevance=100.0, matched_terms=[])]
                if eligible and product is not None
                else []
            )
            result = AgentCatalogResult(
                hits=hits,
                diagnostics=AgentCatalogDiagnostics(
                    total_in_stock=1 if product is not None and product.in_stock else 0,
                    category_matches=1 if product is not None else 0,
                    text_matches=1 if product is not None else 0,
                    eligible_matches=len(hits),
                    lowest_matching_price_paise=(
                        product.offer_price_paise if product is not None else None
                    ),
                    reason="MATCHES_FOUND" if hits else "NO_ELIGIBLE_PRODUCT",
                    applied_categories=[product.category] if product is not None else [],
                    applied_query=state.get("search_query", ""),
                ),
            )
        else:
            result = await self._repository.search_agent_catalog(
                query=state.get("search_query", ""),
                category_slugs=state.get("category_slugs", []),
                allowed_categories=state.get("allowed_categories"),
                max_price_paise=state["intent"].max_price_paise,
                limit=MAX_RETRIEVAL_CANDIDATES,
                exclude_product_ids=state.get("excluded_product_ids", []),
            )

        by_id: dict[UUID, AgentCatalogHit] = {
            hit.product.id: hit for hit in state.get("accumulated_hits", [])
        }
        for hit in result.hits:
            existing = by_id.get(hit.product.id)
            if existing is None or hit.relevance > existing.relevance:
                by_id[hit.product.id] = hit
        accumulated = sorted(
            by_id.values(),
            key=lambda hit: (-hit.relevance, hit.product.title.casefold()),
        )[:MAX_RETRIEVAL_CANDIDATES]
        passes = state.get("retrieval_passes", 0) + 1
        return ShoppingGraphState(
            search_result=result,
            accumulated_hits=accumulated,
            retrieval_passes=passes,
        )

    async def _evaluate_results(
        self, state: ShoppingGraphState
    ) -> ShoppingGraphState:
        hits = state.get("accumulated_hits", [])
        if state.get("forced_product_id") is not None:
            evaluation = RetrievalEvaluation(
                action="FINAL" if hits else "NO_MATCH",
                relevant_product_ids=[hit.product.id for hit in hits[:1]],
                revised_search_query=None,
                revised_category_slugs=[],
                additional_soft_preferences=[],
                clarification_question=None,
                explanation=(
                    "The explicitly referenced product passed current catalogue and policy checks."
                    if hits
                    else "The explicitly referenced product is no longer eligible."
                ),
            )
            return ShoppingGraphState(evaluation=evaluation)

        if self._llm_gateway is None:
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent's reasoning service became unavailable before it could evaluate "
                    "catalogue results. No product or payment action was selected."
                )
            )

        result = state["search_result"]
        try:
            raw = await self._llm_gateway.evaluate_catalogue(
                understanding=state["understanding"].model_dump(mode="json"),
                search_plan=_search_plan_payload(state),
                candidates=[_evaluation_candidate(hit) for hit in hits],
                diagnostics=_diagnostics_payload(result.diagnostics),
                json_schema=RetrievalEvaluation.model_json_schema(),
            )
            evaluation = RetrievalEvaluation.model_validate(raw)
        except (LLMProviderError, ValidationError):
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent could not safely evaluate the catalogue results. "
                    "No recommendation, order, or payment action was created. Please try again."
                )
            )

        available_ids = {hit.product.id for hit in hits}
        if not set(evaluation.relevant_product_ids).issubset(available_ids):
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent selected a product outside the verified catalogue results. "
                    "No product or payment action was accepted."
                )
            )
        known_categories = {category.slug for category in state.get("taxonomy", [])}
        revised_categories = evaluation.revised_category_slugs
        if not set(revised_categories).issubset(known_categories):
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent attempted an unknown catalogue category. "
                    "No product or payment action was accepted."
                )
            )
        allowed_categories = state.get("allowed_categories", [])
        if revised_categories and allowed_categories and not set(
            revised_categories
        ).issubset(allowed_categories):
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent attempted to search outside your saved category allowlist. "
                    "No product or payment action was accepted."
                )
            )
        if evaluation.action == "FINAL" and not evaluation.relevant_product_ids:
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent did not return a verified finalist. No product or payment action "
                    "was created."
                )
            )

        updates = ShoppingGraphState(evaluation=evaluation)
        if evaluation.action == "CLARIFY":
            updates["clarification"] = AgentClarification(
                kind="REQUIREMENTS",
                question=evaluation.clarification_question
                or "Could you clarify which requirement matters most?",
                options=[],
            )
        elif evaluation.action == "REFINE":
            next_query = (
                evaluation.revised_search_query or state.get("search_query", "")
            )
            next_categories = (
                revised_categories or state.get("category_slugs", [])
            )
            next_preferences = list(
                dict.fromkeys(
                    [
                        *state.get("soft_preferences", []),
                        *evaluation.additional_soft_preferences,
                    ]
                )
            )[:12]
            plan_key = _plan_key(next_query, next_categories, next_preferences)
            current_key = _plan_key(
                state.get("search_query", ""),
                state.get("category_slugs", []),
                state.get("soft_preferences", []),
            )
            seen = state.get("seen_plan_keys", [])
            if plan_key == current_key or plan_key in seen:
                updates["evaluation"] = evaluation.model_copy(
                    update={
                        "action": "NO_MATCH",
                        "explanation": (
                            "The proposed refinement repeated an already evaluated search."
                        ),
                    }
                )
            else:
                updates["search_query"] = next_query
                updates["category_slugs"] = next_categories
                updates["soft_preferences"] = next_preferences
                updates["seen_plan_keys"] = [*seen, current_key]
                intent = state["intent"]
                updates["intent"] = intent.model_copy(
                    update={
                        "query": next_query,
                        "category": next_categories[0] if next_categories else None,
                        "preferences": list(
                            dict.fromkeys(
                                [*state.get("hard_requirements", []), *next_preferences]
                            )
                        )[:16],
                    }
                )
        return updates

    def _route_after_evaluation(
        self, state: ShoppingGraphState
    ) -> Literal["retrieve", "compare", "compose"]:
        if state.get("blocked_reason") or state.get("clarification"):
            return "compose"
        evaluation = state.get("evaluation")
        if evaluation is None:
            return "compose"
        if (
            evaluation.action == "REFINE"
            and state.get("retrieval_passes", 0) < MAX_RETRIEVAL_PASSES
        ):
            return "retrieve"
        if evaluation.action == "FINAL" and evaluation.relevant_product_ids:
            return "compare"
        return "compose"

    async def _compare_products(
        self, state: ShoppingGraphState
    ) -> ShoppingGraphState:
        evaluation = state["evaluation"]
        hits_by_id = {
            hit.product.id: hit for hit in state.get("accumulated_hits", [])
        }
        selected_hits = [
            hits_by_id[product_id]
            for product_id in evaluation.relevant_product_ids[:MAX_FINALISTS]
            if product_id in hits_by_id
        ]
        shortlist = [_recommendation_from_hit(hit, state["intent"]) for hit in selected_hits]
        if not shortlist:
            return ShoppingGraphState(
                blocked_reason=(
                    "The verified catalogue shortlist became unavailable. "
                    "No product or payment action was created."
                )
            )

        if state.get("exact_match"):
            exact = shortlist[0]
            decision = AgentProductDecision(
                selected_product_id=exact.product.id,
                ranked_product_ids=[item.product.id for item in shortlist],
                winner_reason=(
                    f"{exact.product.title} is the exact catalogue product selected from this session."
                ),
                tradeoffs=[],
                upsell_product_id=None,
                upsell_reason=None,
                cross_sell_product_id=None,
                cross_sell_reason=None,
                decision_source="deterministic",
            )
        elif self._llm_gateway is None:
            return ShoppingGraphState(
                shortlist=shortlist,
                blocked_reason=(
                    "The Agent's comparison service is unavailable. "
                    "No product or payment action was selected."
                ),
            )
        else:
            try:
                raw = await self._llm_gateway.compare_products(
                    user_text=state["request"].message,
                    intent={
                        "understanding": state["understanding"].model_dump(mode="json"),
                        "effective_intent": state["intent"].model_dump(mode="json"),
                    },
                    candidates=[
                        _comparison_candidate(item.product, role="primary")
                        for item in shortlist
                    ],
                    json_schema=ProductComparisonDecision.model_json_schema(),
                )
                provider_decision = ProductComparisonDecision.model_validate(raw)
                decision = _validate_decision(
                    provider_decision,
                    shortlist,
                    source="openrouter",
                )
            except (LLMProviderError, ValidationError, ValueError):
                return ShoppingGraphState(
                    shortlist=shortlist,
                    blocked_reason=(
                        "The Agent could not complete a grounded comparison of the verified "
                        "products. No order or payment action was created. Please try again."
                    ),
                )

        ordered = _ordered_recommendations(shortlist, decision)
        effective_limit = state.get("effective_limit", state["request"].limit)
        return ShoppingGraphState(
            decision=decision,
            shortlist=ordered,
            recommendations=ordered[:effective_limit],
        )

    def _compose_response(self, state: ShoppingGraphState) -> ShoppingGraphState:
        understanding = state.get("understanding")
        intent = state.get(
            "intent",
            ShoppingIntent(query="", category=None, max_price_paise=None, preferences=[]),
        )
        intent_mode = state.get("intent_mode", "RECOMMEND")
        intent_source = state.get("intent_source", "deterministic_fallback")
        recommendations = state.get("recommendations", [])
        shortlist = state.get("shortlist", [])
        decision = state.get("decision")
        clarification = state.get("clarification")
        blocked_reason = state.get("blocked_reason")
        search_result = state.get("search_result")
        diagnostics = search_result.diagnostics if search_result else None

        winner: AgentRecommendation | None = None
        if decision is not None:
            by_id = {item.product.id: item for item in shortlist}
            selected = by_id.get(decision.selected_product_id)
            if selected is not None:
                winner = _with_decision_reason(selected, decision.winner_reason)

        if clarification is not None:
            reply = clarification.question
            outcome = "CLARIFICATION"
            resolution_kind = "CLARIFICATION_REQUIRED"
        elif blocked_reason:
            reply = blocked_reason
            outcome = "BLOCKED"
            resolution_kind = "NO_MATCH"
        elif intent_mode == "OTHER":
            reply = (
                understanding.other_reply
                if understanding is not None and understanding.other_reply is not None
                else "Hello! What would you like to shop for today?"
            )
            outcome = "CONVERSATION"
            resolution_kind = "CONVERSATION"
        elif winner is not None and decision is not None:
            action = "selected" if intent_mode == "BUY" else "recommend"
            reply = (
                f"I {action} {winner.product.title} at "
                f"{_format_inr(winner.product.offer_price_paise)}. "
                f"{decision.winner_reason}"
            )
            outcome = "RECOMMENDATIONS"
            resolution_kind = (
                "EXACT_MATCH" if state.get("exact_match") else "ALTERNATIVES"
            )
        else:
            reply = _no_match_reply(diagnostics, intent)
            outcome = "NO_MATCH"
            resolution_kind = "NO_MATCH"

        public_diagnostics = (
            CatalogSearchDiagnostics(**_diagnostics_payload(diagnostics))
            if diagnostics is not None
            else None
        )
        return ShoppingGraphState(
            response=AgentChatResponse(
                reply=reply,
                intent_source=intent_source,
                intent_mode=intent_mode,
                decision_source=decision.decision_source if decision else None,
                intent=intent,
                recommendations=recommendations,
                winner=winner,
                decision=decision,
                upsell=None,
                cross_sell=None,
                account_controls_applied=state.get("controls_applied", False),
                notice="",
                outcome=outcome,
                resolution_kind=resolution_kind,
                clarification=clarification,
                focus_product_id=winner.product.id if winner else None,
                exact_match=winner is not None and bool(state.get("exact_match")),
                evaluated_count=diagnostics.total_in_stock if diagnostics else 0,
                eligible_count=diagnostics.eligible_matches if diagnostics else 0,
                cross_sell_consent_required=False,
                retrieval_passes=state.get("retrieval_passes", 0),
                search_diagnostics=public_diagnostics,
            )
        )


def _previous_intent(context: dict[str, object]) -> ShoppingIntent | None:
    value = context.get("intent")
    if not isinstance(value, dict):
        return None
    try:
        return ShoppingIntent.model_validate(value)
    except ValueError:
        return None


def _reference_clarification(
    question: str, products: list[CatalogProduct]
) -> AgentClarification:
    return AgentClarification(
        kind="REFERENCE",
        question=question,
        options=[
            ClarificationOption(product_id=product.id, label=product.title)
            for product in products[:4]
        ],
    )


def _compact_product(product: CatalogProduct) -> dict[str, object]:
    return {
        "product_id": str(product.id),
        "title": product.title,
        "brand": product.brand,
        "model": product.model,
        "category": product.category,
        "price_inr": product.offer_price_paise // 100,
    }


def _evaluation_candidate(hit: AgentCatalogHit) -> dict[str, object]:
    product = hit.product
    return {
        "product_id": str(product.id),
        "title": product.title,
        "brand": product.brand,
        "model": product.model,
        "category": product.category,
        "price_inr": product.offer_price_paise // 100,
        "description": product.description[:400],
        "specifications": product.specifications,
        "search_tags": product.search_tags,
        "matched_terms": hit.matched_terms,
        "retrieval_relevance": round(hit.relevance, 4),
    }


def _search_plan_payload(state: ShoppingGraphState) -> dict[str, object]:
    return {
        "query": state.get("search_query", ""),
        "category_slugs": state.get("category_slugs", []),
        "maximum_price_inr": (
            state["intent"].max_price_paise // 100
            if state["intent"].max_price_paise is not None
            else None
        ),
        "hard_requirements": state.get("hard_requirements", []),
        "soft_preferences": state.get("soft_preferences", []),
        "excluded_product_ids": [
            str(product_id) for product_id in state.get("excluded_product_ids", [])
        ],
        "retrieval_pass": state.get("retrieval_passes", 0),
        "maximum_retrieval_passes": MAX_RETRIEVAL_PASSES,
    }


def _diagnostics_payload(
    diagnostics: AgentCatalogDiagnostics,
) -> dict[str, object]:
    return {
        "total_in_stock": diagnostics.total_in_stock,
        "category_matches": diagnostics.category_matches,
        "text_matches": diagnostics.text_matches,
        "eligible_matches": diagnostics.eligible_matches,
        "lowest_matching_price_paise": diagnostics.lowest_matching_price_paise,
        "reason": diagnostics.reason,
        "applied_categories": diagnostics.applied_categories,
        "applied_query": diagnostics.applied_query,
    }


def _plan_key(query: str, categories: list[str], preferences: list[str]) -> str:
    return "|".join(
        [
            " ".join(query.casefold().split()),
            ",".join(sorted(categories)),
            ",".join(sorted(preferences)),
        ]
    )


def _recommendation_from_hit(
    hit: AgentCatalogHit, intent: ShoppingIntent
) -> AgentRecommendation:
    reasons: list[str] = []
    if hit.matched_terms:
        reasons.append(f"Catalogue match: {', '.join(hit.matched_terms[:4])}")
    if intent.category is not None and hit.product.category == intent.category:
        reasons.append(f"Matches the {intent.category} category")
    if intent.max_price_paise is not None:
        reasons.append(f"Within the {_format_inr(intent.max_price_paise)} maximum")
    reasons.append(f"Verified in stock: {hit.product.inventory_quantity} available")
    score = max(1, min(100, round(60 + min(hit.relevance, 4.0) * 10)))
    return AgentRecommendation(
        product=CatalogProduct.model_validate(hit.product),
        score=score,
        reasons=reasons[:6],
    )


def _comparison_candidate(product: CatalogProduct, *, role: str) -> dict[str, object]:
    return {
        "id": str(product.id),
        "role": role,
        "sku": product.sku,
        "brand": product.brand,
        "model": product.model,
        "category": product.category,
        "title": product.title,
        "description": product.description,
        "offer_price_inr": product.offer_price_paise / 100,
        "mrp_inr": product.mrp_paise / 100 if product.mrp_paise else None,
        "inventory_quantity": product.inventory_quantity,
        "specifications": product.specifications,
        "search_tags": product.search_tags,
        "specifications_verified_at": product.specifications_verified_at.isoformat(),
        "version": product.version,
    }


def _validate_decision(
    decision: ProductComparisonDecision,
    primary: list[AgentRecommendation],
    *,
    source: AgentDecisionSource,
) -> AgentProductDecision:
    primary_ids = {item.product.id for item in primary}
    if decision.selected_product_id not in primary_ids:
        raise ValueError("The model selected an unknown product")

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
    return AgentProductDecision(
        selected_product_id=decision.selected_product_id,
        ranked_product_ids=ranked_ids[:MAX_FINALISTS],
        winner_reason=decision.winner_reason,
        tradeoffs=decision.tradeoffs,
        upsell_product_id=upsell_id,
        upsell_reason=decision.upsell_reason if upsell_id is not None else None,
        cross_sell_product_id=None,
        cross_sell_reason=None,
        decision_source=source,
    )


def _ordered_recommendations(
    primary: list[AgentRecommendation], decision: AgentProductDecision
) -> list[AgentRecommendation]:
    by_id = {item.product.id: item for item in primary}
    return [
        by_id[product_id]
        for product_id in decision.ranked_product_ids
        if product_id in by_id
    ]


def _with_decision_reason(
    recommendation: AgentRecommendation, reason: str
) -> AgentRecommendation:
    return recommendation.model_copy(
        update={"reasons": [reason, *recommendation.reasons][:6]}
    )


def _no_match_reply(
    diagnostics: AgentCatalogDiagnostics | None, intent: ShoppingIntent
) -> str:
    if diagnostics is None:
        return (
            "I could not complete a verified catalogue search for that request. "
            "No product, order, or payment action was created."
        )
    if diagnostics.reason == "OVER_BUDGET" and diagnostics.lowest_matching_price_paise:
        budget = (
            _format_inr(intent.max_price_paise)
            if intent.max_price_paise is not None
            else "your maximum"
        )
        return (
            f"I found relevant in-stock products, but none fit {budget}. "
            f"The lowest matching price is {_format_inr(diagnostics.lowest_matching_price_paise)}. "
            "I will not raise your budget without your permission."
        )
    if diagnostics.reason == "NO_CATEGORY_MATCH":
        return (
            "The live catalogue has no in-stock products in the requested category under the "
            "current account controls. No order or payment action was created."
        )
    if diagnostics.reason == "NO_TEXT_MATCH":
        return (
            "I searched the complete live catalogue but found no product matching that need. "
            "Try describing the use case differently or tell me which requirement can change."
        )
    return (
        "I searched the complete live catalogue but found no in-stock product satisfying the "
        "current requirements and limits. No order or payment action was created."
    )


def _format_inr(paise: int) -> str:
    rupees, remainder = divmod(paise, 100)
    return f"₹{rupees:,}" if remainder == 0 else f"₹{rupees:,}.{remainder:02d}"
