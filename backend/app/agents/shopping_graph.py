"""LLM-first catalogue research and grounded product selection with LangGraph."""

from __future__ import annotations

import re
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
    AgentSessionState,
    ClarificationOption,
    ProductComparisonDecision,
    RetrievalEvaluation,
    ShoppingIntent,
    ShoppingUnderstanding,
)
from app.schemas.catalog import CatalogProduct, CatalogSearchDiagnostics
from app.services.agent_turn_policy import (
    classify_turn,
    context_uuid_list,
    extract_excluded_brands,
    family_search_query,
    identity_similarity,
    infer_requested_brand,
    requested_identity_name,
    resolve_reference_ids,
    safe_memory_reply,
    safe_payment_status_reply,
)

MAX_RETRIEVAL_PASSES = 3
MAX_RETRIEVAL_CANDIDATES = 40
MAX_FINALISTS = 8


class ShoppingGraphState(TypedDict, total=False):
    request: AgentChatRequest
    taxonomy: list[CatalogCategoryDescriptor]
    identity_products: list[CatalogProduct]
    understanding: ShoppingUnderstanding
    intent: ShoppingIntent
    intent_mode: AgentIntentMode
    turn_action: str
    intent_source: AgentIntentSource
    conversation_reply: str
    category_slugs: list[str]
    search_query: str
    soft_preferences: list[str]
    hard_requirements: list[str]
    excluded_terms: list[str]
    preferred_brands: list[str]
    required_brand: str
    requested_count: int
    unavailable_product_name: str
    exact_requested_product: str
    similarity_fallback: bool
    evidence_limited: bool
    allowed_categories: list[str]
    forced_product_id: UUID
    forced_product_ids: list[UUID]
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
        identity_products = [
            CatalogProduct.model_validate(product)
            for product in await self._repository.list_agent_identity_catalog()
        ]
        return ShoppingGraphState(
            taxonomy=taxonomy,
            identity_products=identity_products,
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
        policy = classify_turn(request.message)
        previous_intent = _previous_intent(self._conversation_context)
        default_intent = ShoppingIntent(
            query=previous_intent.query if previous_intent else "",
            category=request.category or (previous_intent.category if previous_intent else None),
            max_price_paise=request.max_price_paise
            or (previous_intent.max_price_paise if previous_intent else None),
            preferences=previous_intent.preferences if previous_intent else [],
        )

        memory_reply = safe_memory_reply(
            request.message,
            self._conversation_context,
            self._reference_products,
        )
        if policy.payment_status_request:
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="OTHER",
                turn_action="OTHER",
                intent_source="deterministic",
                conversation_reply=safe_payment_status_reply(),
            )
        if policy.action == "CANCEL":
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="OTHER",
                turn_action="CANCEL",
                intent_source="deterministic",
                conversation_reply=(
                    "Understood. I cancelled that selection and will not prepare a checkout. "
                    "Tell me if you want to keep browsing."
                ),
            )
        if memory_reply is not None:
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="OTHER",
                turn_action="MEMORY",
                intent_source="deterministic",
                conversation_reply=memory_reply,
            )

        allowed_reference_ids = {product.id for product in self._reference_products}
        deterministic_references = resolve_reference_ids(
            request.message,
            self._conversation_context,
            self._reference_products,
        )
        if (
            request.selected_product_id is not None
            and request.selected_product_id in allowed_reference_ids
        ):
            deterministic_references = [request.selected_product_id]
        if deterministic_references:
            referenced = {
                product.id: product for product in self._reference_products
            }
            selected_products = [
                referenced[identifier]
                for identifier in deterministic_references
                if identifier in referenced
            ]
            categories = list(
                dict.fromkeys(product.category for product in selected_products)
            )
            mode: AgentIntentMode = (
                "BUY"
                if policy.action == "BUY"
                else "COMPARE"
                if policy.action == "COMPARE" and len(selected_products) > 1
                else "RECOMMEND"
            )
            hard_requirements = _context_strings(
                self._conversation_context, "hard_requirements"
            )
            soft_preferences = _context_strings(
                self._conversation_context, "soft_preferences"
            )
            excluded_product_ids = context_uuid_list(
                self._conversation_context, "excluded_product_ids"
            )
            intent = ShoppingIntent(
                query=(
                    previous_intent.query
                    if previous_intent is not None
                    else " ".join(product.title for product in selected_products)
                ),
                category=(
                    categories[0]
                    if len(categories) == 1
                    else previous_intent.category
                    if previous_intent is not None
                    else None
                ),
                max_price_paise=(
                    previous_intent.max_price_paise
                    if previous_intent is not None
                    else request.max_price_paise
                ),
                preferences=_merge_strings(
                    hard_requirements, soft_preferences
                )[:16],
            )
            result = ShoppingGraphState(
                intent=intent,
                intent_mode=mode,
                turn_action=str(policy.action or mode),
                intent_source="deterministic",
                category_slugs=categories,
                search_query=intent.query,
                hard_requirements=hard_requirements,
                soft_preferences=soft_preferences,
                excluded_terms=_context_strings(
                    self._conversation_context, "excluded_terms"
                ),
                preferred_brands=_context_strings(
                    self._conversation_context, "preferred_brands"
                ),
                excluded_product_ids=excluded_product_ids,
                forced_product_ids=deterministic_references,
                forced_product_id=deterministic_references[0],
                exact_match=len(deterministic_references) == 1,
            )
            requested_count = policy.requested_count or _context_int(
                self._conversation_context, "requested_count"
            )
            if requested_count is not None:
                result["requested_count"] = max(1, min(requested_count, 8))
            return result

        if self._llm_gateway is None:
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="RECOMMEND",
                turn_action="RECOMMEND",
                intent_source="deterministic_fallback",
                blocked_reason=(
                    "The Agent's reasoning service is temporarily unavailable. "
                    "No recommendation, order, or payment action was created."
                ),
            )

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
            "structured_session_state": {
                key: self._conversation_context.get(key)
                for key in (
                    "hard_requirements",
                    "soft_preferences",
                    "excluded_terms",
                    "preferred_brands",
                    "budget_relationship",
                    "budget_minimum_paise",
                    "budget_maximum_paise",
                    "requested_count",
                    "active_candidate_ids",
                    "last_compared_ids",
                    "focus_product_id",
                )
            },
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
            fallback_category = request.category or _infer_category_from_message(
                request.message, state.get("taxonomy", [])
            )
            fallback_ceiling = request.max_price_paise or _parse_max_price_paise(
                request.message
            )
            if policy.action == "RECOMMEND" and fallback_category is not None:
                continuing_fallback = policy.is_refinement or _is_continuation_message(
                    request.message
                )
                hard_requirements = (
                    _context_strings(self._conversation_context, "hard_requirements")
                    if continuing_fallback
                    else []
                )
                soft_preferences = (
                    _context_strings(self._conversation_context, "soft_preferences")
                    if continuing_fallback
                    else []
                )
                excluded_terms = _merge_strings(
                    _context_strings(self._conversation_context, "excluded_terms")
                    if continuing_fallback
                    else [],
                    extract_excluded_brands(
                        request.message, state.get("identity_products", [])
                    ),
                )
                intent = ShoppingIntent(
                    query=fallback_category,
                    category=fallback_category,
                    max_price_paise=(
                        fallback_ceiling
                        or (
                            previous_intent.max_price_paise
                            if continuing_fallback and previous_intent is not None
                            else None
                        )
                    ),
                    preferences=_merge_strings(
                        hard_requirements, soft_preferences
                    )[:16],
                )
                fallback = ShoppingGraphState(
                    intent=intent,
                    intent_mode="RECOMMEND",
                    turn_action="RECOMMEND",
                    intent_source="deterministic_fallback",
                    category_slugs=[fallback_category],
                    search_query=fallback_category,
                    hard_requirements=hard_requirements,
                    soft_preferences=soft_preferences,
                    excluded_terms=excluded_terms,
                    preferred_brands=_context_strings(
                        self._conversation_context, "preferred_brands"
                    ),
                    excluded_product_ids=(
                        context_uuid_list(
                            self._conversation_context, "excluded_product_ids"
                        )
                        if continuing_fallback
                        else []
                    ),
                )
                if policy.requested_count is not None:
                    fallback["requested_count"] = policy.requested_count
                return fallback
            return ShoppingGraphState(
                intent=default_intent,
                intent_mode="RECOMMEND",
                turn_action="RECOMMEND",
                intent_source="deterministic_fallback",
                clarification=AgentClarification(
                    kind="REQUIREMENTS",
                    question=(
                        "I could not safely resolve every part of that request. "
                        "Which product category and maximum budget should I use?"
                    ),
                    options=[],
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
                turn_action="RECOMMEND",
                intent_source="openrouter",
                clarification=AgentClarification(
                    kind="REQUIREMENTS",
                    question="Which type of product would you like me to search for?",
                    options=[],
                ),
            )
        invalid_provider_reference = not returned_reference_ids.issubset(
            allowed_reference_ids
        ) or not returned_exclusion_ids.issubset(allowed_reference_ids)
        if invalid_provider_reference and not deterministic_references:
            return ShoppingGraphState(
                understanding=understanding,
                intent=default_intent,
                intent_mode="RECOMMEND",
                turn_action="RECOMMEND",
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
                turn_action="RECOMMEND",
                intent_source="openrouter",
                clarification=_reference_clarification(
                    "That product is not one of the verified options in this session. Which product did you mean?",
                    self._reference_products,
                ),
            )

        forced_product_ids: list[UUID] = []
        if selected_product_id is not None:
            forced_product_ids = [selected_product_id]
        elif deterministic_references:
            forced_product_ids = deterministic_references
        elif understanding.reference_status == "RESOLVED":
            forced_product_ids = list(understanding.referenced_product_ids)

        latest_available_requested = _requests_latest_available(request.message)
        proposed_question = understanding.clarification_question or (
            "Which of the products from the current session did you mean?"
        )
        if (
            understanding.needs_clarification
            or understanding.reference_status in {"AMBIGUOUS", "INVALID"}
        ) and (
            forced_product_ids
            or latest_available_requested
            or _repeats_recent_clarification(
                proposed_question,
                self._conversation_context,
            )
        ):
            understanding = understanding.model_copy(
                update={
                    "needs_clarification": False,
                    "clarification_question": None,
                    "reference_status": (
                        "RESOLVED" if forced_product_ids else "NONE"
                    ),
                    "referenced_product_ids": forced_product_ids,
                }
            )

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
        previous_ceiling = _context_int(
            self._conversation_context, "budget_maximum_paise"
        ) or (previous_intent.max_price_paise if previous_intent is not None else None)
        continuing = bool(
            understanding.intent_mode == "REFINE"
            or forced_product_ids
            or policy.is_refinement
            or _is_continuation_message(request.message)
        )
        max_price_paise = (
            request.max_price_paise
            or provider_ceiling
            or (previous_ceiling if continuing else None)
        )
        categories = list(understanding.category_slugs)
        if request.category is not None:
            categories = [request.category]
        elif not categories and continuing and previous_intent and previous_intent.category:
            categories = [previous_intent.category]

        search_query = understanding.search_query
        if (
            (not search_query or _is_reference_only_query(search_query))
            and continuing
            and previous_intent is not None
        ):
            search_query = previous_intent.query
        if (
            latest_available_requested
            and previous_intent is not None
            and _is_latest_only_followup(request.message)
        ):
            search_query = family_search_query(previous_intent.query, None) or search_query

        identity_products = state.get("identity_products", [])
        previous_hard = _context_strings(self._conversation_context, "hard_requirements")
        previous_soft = _context_strings(self._conversation_context, "soft_preferences")
        hard_requirements = _merge_strings(
            previous_hard if continuing else [],
            understanding.hard_requirements,
        )
        soft_preferences = _merge_strings(
            previous_soft if continuing else [],
            understanding.soft_preferences,
        )
        if latest_available_requested:
            soft_preferences = _merge_strings(
                soft_preferences, ["latest available model"]
            )
        deterministic_excluded_brands = extract_excluded_brands(
            request.message, identity_products
        )
        excluded_terms = _merge_strings(
            _context_strings(self._conversation_context, "excluded_terms")
            if continuing
            else [],
            _merge_strings(
                list(understanding.excluded_terms),
                deterministic_excluded_brands,
            ),
        )
        excluded_product_ids = _merge_uuids(
            context_uuid_list(self._conversation_context, "excluded_product_ids")
            if continuing
            else [],
            list(understanding.excluded_product_ids),
        )
        if policy.no_repeat:
            excluded_product_ids = _merge_uuids(
                excluded_product_ids,
                context_uuid_list(self._conversation_context, "shown_product_ids"),
            )

        intent_mode = understanding.intent_mode
        turn_action = policy.action
        if turn_action == "BUY":
            intent_mode = "BUY"
        elif turn_action == "COMPARE":
            intent_mode = "COMPARE"
        elif turn_action == "RECOMMEND":
            intent_mode = "RECOMMEND"
        elif turn_action == "OTHER":
            intent_mode = "OTHER"
        elif intent_mode == "REFINE":
            intent_mode = "RECOMMEND"
        if (
            selected_product_id is not None
            and self._conversation_context.get("pending_buy") is True
        ):
            intent_mode = "BUY"
            turn_action = "BUY"

        exact_requested_product: str | None = None
        unavailable_product_name: str | None = None
        required_brand: str | None = None
        similarity_fallback = False
        if not forced_product_ids and intent_mode != "COMPARE":
            exact_requested_product = requested_identity_name(
                request.message, search_query
            )
            if exact_requested_product is not None:
                exact_product = _find_exact_identity(
                    exact_requested_product, identity_products
                )
                if exact_product is not None and exact_product.in_stock:
                    forced_product_ids = [exact_product.id]
                    categories = [exact_product.category]
                    required_brand = exact_product.brand
                else:
                    unavailable_product_name = exact_requested_product
                    required_brand = infer_requested_brand(
                        exact_requested_product, identity_products
                    )
                    search_query = family_search_query(
                        exact_requested_product, required_brand
                    )
                    intent_mode = "RECOMMEND"
                    turn_action = "RECOMMEND"
                    similarity_fallback = True

        preferred_brands = _preferred_brands(
            [*hard_requirements, *soft_preferences],
            identity_products,
            _context_strings(self._conversation_context, "preferred_brands")
            if continuing
            else [],
        )
        if required_brand is None and len(preferred_brands) == 1:
            required_brand = preferred_brands[0]

        requested_count = policy.requested_count or (
            _context_int(self._conversation_context, "requested_count")
            if continuing
            else None
        )
        if requested_count is not None:
            requested_count = max(1, min(requested_count, 8))
        preferences = _merge_strings(hard_requirements, soft_preferences)[:16]
        if not preferences and continuing and previous_intent is not None:
            preferences = previous_intent.preferences
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
            turn_action=str(turn_action or intent_mode),
            intent_source="openrouter",
            category_slugs=categories,
            search_query=search_query,
            soft_preferences=soft_preferences,
            hard_requirements=hard_requirements,
            excluded_terms=excluded_terms,
            preferred_brands=preferred_brands,
            excluded_product_ids=excluded_product_ids,
            exact_match=bool(
                len(forced_product_ids) == 1 and unavailable_product_name is None
            ),
            similarity_fallback=similarity_fallback,
        )
        if requested_count is not None:
            result["requested_count"] = requested_count
        if forced_product_ids:
            result["forced_product_ids"] = forced_product_ids
            result["forced_product_id"] = forced_product_ids[0]
        if exact_requested_product is not None:
            result["exact_requested_product"] = exact_requested_product
        if unavailable_product_name is not None:
            result["unavailable_product_name"] = unavailable_product_name
        if required_brand is not None:
            result["required_brand"] = required_brand
        if clarification is not None and not forced_product_ids:
            result["clarification"] = clarification
        return result

    def _apply_controls(self, state: ShoppingGraphState) -> ShoppingGraphState:
        if state.get("blocked_reason") or state.get("clarification"):
            return ShoppingGraphState()
        if state.get("intent_mode") == "OTHER":
            return ShoppingGraphState()
        request = state["request"]
        requested_count = state.get("requested_count", request.limit)
        controls = self._controls
        if controls is None:
            return ShoppingGraphState(
                effective_limit=min(request.limit, requested_count),
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
            effective_limit=min(
                request.limit, requested_count, controls.max_recommendations
            ),
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
        forced_product_ids = state.get("forced_product_ids", [])
        if not forced_product_ids and state.get("forced_product_id") is not None:
            forced_product_ids = [state["forced_product_id"]]
        if forced_product_ids:
            products = await self._repository.get_active_many(forced_product_ids)
            by_id = {product.id: product for product in products}
            intent = state["intent"]
            allowed = state.get("allowed_categories", [])
            requested_categories = state.get("category_slugs", [])
            excluded_ids = set(state.get("excluded_product_ids", []))
            hits: list[AgentCatalogHit] = []
            for identifier in forced_product_ids:
                product = by_id.get(identifier)
                eligible = (
                    product is not None
                    and product.in_stock
                    and (not allowed or product.category in allowed)
                    and (
                        not requested_categories
                        or product.category in requested_categories
                    )
                    and (
                        intent.max_price_paise is None
                        or product.offer_price_paise <= intent.max_price_paise
                    )
                    and product.id not in excluded_ids
                    and not _product_matches_excluded_terms(
                        CatalogProduct.model_validate(product),
                        state.get("excluded_terms", []),
                    )
                )
                if eligible and product is not None:
                    hits.append(
                        AgentCatalogHit(
                            product=product,
                            relevance=100.0,
                            matched_terms=[],
                        )
                    )
            result = AgentCatalogResult(
                hits=hits,
                diagnostics=AgentCatalogDiagnostics(
                    total_in_stock=sum(1 for product in products if product.in_stock),
                    category_matches=len(products),
                    text_matches=len(products),
                    eligible_matches=len(hits),
                    lowest_matching_price_paise=(
                        min(product.offer_price_paise for product in products)
                        if products
                        else None
                    ),
                    reason="MATCHES_FOUND" if hits else "NO_ELIGIBLE_PRODUCT",
                    applied_categories=list(
                        dict.fromkeys(product.category for product in products)
                    ),
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
                required_brand=state.get("required_brand"),
                excluded_terms=state.get("excluded_terms", []),
            )

        hits = list(result.hits)
        unavailable = state.get("unavailable_product_name")
        if unavailable is not None:
            hits.sort(
                key=lambda hit: (
                    -identity_similarity(
                        unavailable,
                        CatalogProduct.model_validate(hit.product),
                    ),
                    hit.product.offer_price_paise,
                    hit.product.title.casefold(),
                )
            )
            result = AgentCatalogResult(hits=hits, diagnostics=result.diagnostics)
        # A refined plan replaces stale candidates; it never unions products from an older scope.
        accumulated = hits[:MAX_RETRIEVAL_CANDIDATES]
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
        if state.get("forced_product_ids"):
            evaluation = RetrievalEvaluation(
                action="FINAL" if hits else "NO_MATCH",
                relevant_product_ids=[
                    hit.product.id for hit in hits[:MAX_FINALISTS]
                ],
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

        unavailable = state.get("unavailable_product_name")
        if unavailable is not None:
            limit = state.get("effective_limit", state["request"].limit)
            evaluation = RetrievalEvaluation(
                action="FINAL" if hits else "NO_MATCH",
                relevant_product_ids=[hit.product.id for hit in hits[:limit]],
                revised_search_query=None,
                revised_category_slugs=[],
                additional_soft_preferences=[],
                clarification_question=None,
                explanation=(
                    f"{unavailable} is unavailable; alternatives are ordered by verified identity similarity."
                    if hits
                    else f"{unavailable} is unavailable and no eligible same-brand alternative was found."
                ),
            )
            return ShoppingGraphState(evaluation=evaluation)

        if state.get("intent_source") == "deterministic_fallback":
            limit = state.get("effective_limit", state["request"].limit)
            evaluation = RetrievalEvaluation(
                action="FINAL" if hits else "NO_MATCH",
                relevant_product_ids=[hit.product.id for hit in hits[:limit]],
                revised_search_query=None,
                revised_category_slugs=[],
                additional_soft_preferences=[],
                clarification_question=None,
                explanation=(
                    "Verified catalogue matches satisfy the deterministic browsing constraints."
                    if hits
                    else "No verified catalogue match satisfies the deterministic browsing constraints."
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
        if evaluation.action == "NO_MATCH" and hits:
            evaluation = evaluation.model_copy(
                update={
                    "action": "FINAL",
                    "relevant_product_ids": [
                        hit.product.id
                        for hit in hits[
                            : state.get("effective_limit", state["request"].limit)
                        ]
                    ],
                    "revised_search_query": None,
                    "revised_category_slugs": [],
                    "clarification_question": None,
                    "explanation": (
                        "Verified eligible products exist, so return the available partial result "
                        "instead of treating requested quantity as an eligibility requirement."
                    ),
                }
            )
        if evaluation.action == "FINAL" and not evaluation.relevant_product_ids:
            return ShoppingGraphState(
                blocked_reason=(
                    "The Agent did not return a verified finalist. No product or payment action "
                    "was created."
                )
            )

        if (
            evaluation.action == "REFINE"
            and state.get("retrieval_passes", 0) >= MAX_RETRIEVAL_PASSES
            and hits
        ):
            evaluation = evaluation.model_copy(
                update={
                    "action": "FINAL",
                    "relevant_product_ids": [
                        hit.product.id
                        for hit in hits[
                            : state.get("effective_limit", state["request"].limit)
                        ]
                    ],
                    "revised_search_query": None,
                    "revised_category_slugs": [],
                    "clarification_question": None,
                    "explanation": (
                        "The bounded search is complete; return the verified partial matches."
                    ),
                }
            )

        if (
            evaluation.action == "FINAL"
            and not state.get("forced_product_ids")
            and not state.get("similarity_fallback")
            and hits
            and _should_diversify_hits(state, hits)
        ):
            evaluation = evaluation.model_copy(
                update={
                    "relevant_product_ids": _diverse_hit_ids(
                        hits,
                        state.get("effective_limit", state["request"].limit),
                    ),
                    "explanation": (
                        "The catalogue lacks verified evidence for an objective feature winner, "
                        "so present a brand-diverse verified shortlist."
                    ),
                }
            )

        latest_available = "latest available model" in state.get(
            "soft_preferences", []
        )
        if latest_available and not hits and evaluation.action in {"CLARIFY", "NO_MATCH"}:
            current_query = state.get("search_query", "")
            family_query = _without_generation_tokens(current_query)
            if family_query and family_query.casefold() != current_query.casefold():
                evaluation = evaluation.model_copy(
                    update={
                        "action": "REFINE",
                        "revised_search_query": family_query,
                        "additional_soft_preferences": ["latest available model"],
                        "clarification_question": None,
                        "explanation": (
                            "The exact generation was unavailable, so search the verified "
                            "product family for the latest available model."
                        ),
                    }
                )

        if evaluation.action == "CLARIFY":
            clarification_question = evaluation.clarification_question or (
                "Could you clarify which requirement matters most?"
            )
            repeated = _repeats_recent_clarification(
                clarification_question,
                self._conversation_context,
            )
            if (latest_available or repeated) and hits:
                finalist_ids = evaluation.relevant_product_ids or [
                    hit.product.id for hit in hits[:MAX_FINALISTS]
                ]
                evaluation = evaluation.model_copy(
                    update={
                        "action": "FINAL",
                        "relevant_product_ids": finalist_ids,
                        "clarification_question": None,
                        "explanation": (
                            "Resolved the latest-available fallback from verified catalogue "
                            "candidates instead of repeating a clarification."
                        ),
                    }
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
                if hits:
                    updates["evaluation"] = evaluation.model_copy(
                        update={
                            "action": "FINAL",
                            "relevant_product_ids": [
                                hit.product.id
                                for hit in hits[
                                    : state.get(
                                        "effective_limit", state["request"].limit
                                    )
                                ]
                            ],
                            "revised_search_query": None,
                            "revised_category_slugs": [],
                            "clarification_question": None,
                            "explanation": (
                                "The refinement repeated a completed search, so return the "
                                "verified partial matches already available."
                            ),
                        }
                    )
                else:
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

        effective_limit = state.get("effective_limit", state["request"].limit)
        if _should_present_unranked(state, shortlist):
            ordered = sorted(
                shortlist,
                key=lambda item: (
                    item.product.brand.casefold(),
                    item.product.model.casefold(),
                    item.product.offer_price_paise,
                ),
            )
            return ShoppingGraphState(
                shortlist=ordered,
                recommendations=ordered[:effective_limit],
                evidence_limited=True,
            )

        if state.get("exact_match"):
            exact = shortlist[0]
            decision = _deterministic_decision(
                shortlist,
                exact.product.id,
                (
                    f"{exact.product.title} is the exact in-stock catalogue product "
                    "selected from this session."
                ),
            )
        elif state.get("similarity_fallback"):
            closest = shortlist[0]
            unavailable = state.get("unavailable_product_name", "The requested product")
            decision = _deterministic_decision(
                shortlist,
                closest.product.id,
                (
                    f"{closest.product.title} is the closest verified same-brand identity "
                    f"match to {unavailable}; it is in stock at "
                    f"{_format_inr(closest.product.offer_price_paise)}."
                ),
            )
        elif len(shortlist) == 1:
            only = shortlist[0]
            decision = _deterministic_decision(
                shortlist,
                only.product.id,
                _grounded_winner_reason(only, shortlist, state),
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
                        "understanding": (
                            state["understanding"].model_dump(mode="json")
                            if state.get("understanding") is not None
                            else {}
                        ),
                        "effective_intent": state["intent"].model_dump(mode="json"),
                    },
                    candidates=[
                        _comparison_candidate(item.product, role="primary")
                        for item in shortlist
                    ],
                    json_schema=ProductComparisonDecision.model_json_schema(),
                )
                provider_decision = ProductComparisonDecision.model_validate(raw)
                selected = _validate_decision(
                    provider_decision,
                    shortlist,
                    source="openrouter",
                )
                selected_item = next(
                    item
                    for item in shortlist
                    if item.product.id == selected.selected_product_id
                )
                decision = selected.model_copy(
                    update={
                        "winner_reason": _grounded_winner_reason(
                            selected_item, shortlist, state
                        ),
                        "tradeoffs": [],
                        "upsell_product_id": None,
                        "upsell_reason": None,
                    }
                )
            except (LLMProviderError, ValidationError, ValueError, StopIteration):
                ordered = sorted(
                    shortlist,
                    key=lambda item: (
                        item.product.brand.casefold(),
                        item.product.model.casefold(),
                    ),
                )
                return ShoppingGraphState(
                    shortlist=ordered,
                    recommendations=ordered[:effective_limit],
                    evidence_limited=True,
                )

        ordered = _ordered_recommendations(shortlist, decision)
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
        conversation_reply = state.get("conversation_reply")
        search_result = state.get("search_result")
        diagnostics = search_result.diagnostics if search_result else None
        unavailable = state.get("unavailable_product_name")

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
        elif conversation_reply is not None:
            reply = conversation_reply
            outcome = "CONVERSATION"
            resolution_kind = "CONVERSATION"
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
        elif intent_mode == "COMPARE" and recommendations:
            reply = _grounded_comparison_reply(
                recommendations,
                winner,
                state["request"].message,
            )
            outcome = "RECOMMENDATIONS"
            resolution_kind = "ALTERNATIVES"
        elif state.get("evidence_limited") and recommendations:
            names = ", ".join(
                f"{item.product.title} at {_format_inr(item.product.offer_price_paise)}"
                for item in recommendations
            )
            reply = (
                f"I found {len(recommendations)} verified in-stock options: {names}. "
                "The catalogue does not contain enough verified feature evidence to honestly "
                "declare one objectively best for that preference, so I have not prepared a "
                "checkout. Tell me which option you want to compare or buy."
            )
            outcome = "RECOMMENDATIONS"
            resolution_kind = "ALTERNATIVES"
        elif winner is not None and decision is not None:
            action = "selected" if intent_mode == "BUY" else "recommend"
            selection = (
                f"I {action} {winner.product.title} at "
                f"{_format_inr(winner.product.offer_price_paise)}. "
                f"{decision.winner_reason}"
            )
            if unavailable is not None:
                reply = (
                    f"Sorry, {unavailable} is not available in the live catalogue. "
                    f"I did not silently substitute or prepare checkout. {selection}"
                )
            else:
                reply = selection
            outcome = "RECOMMENDATIONS"
            resolution_kind = (
                "EXACT_MATCH" if state.get("exact_match") else "ALTERNATIVES"
            )
        elif unavailable is not None:
            reply = (
                f"Sorry, {unavailable} is not available in the live catalogue, and I found no "
                "eligible same-brand alternative within the current category, budget, and "
                "account controls. No checkout was prepared."
            )
            outcome = "NO_MATCH"
            resolution_kind = "NO_MATCH"
        elif classify_turn(state["request"].message).no_repeat:
            reply = (
                "I found no additional in-stock product that satisfies the same constraints "
                "without repeating something already shown. Tell me which requirement may change."
            )
            outcome = "NO_MATCH"
            resolution_kind = "NO_MATCH"
        else:
            reply = _no_match_reply(diagnostics, intent)
            outcome = "NO_MATCH"
            resolution_kind = "NO_MATCH"

        requested_count = state.get("requested_count")
        if (
            outcome == "RECOMMENDATIONS"
            and requested_count is not None
            and recommendations
            and len(recommendations) < requested_count
            and not state.get("exact_match")
            and not state.get("forced_product_ids")
            and not state.get("similarity_fallback")
        ):
            reply += (
                f" You asked for {requested_count}, but only {len(recommendations)} verified "
                "option(s) satisfied the current constraints."
            )

        public_diagnostics = (
            CatalogSearchDiagnostics(**_diagnostics_payload(diagnostics))
            if diagnostics is not None
            else None
        )
        budget_relationship = (
            understanding.budget.relationship
            if understanding is not None
            else str(self._conversation_context.get("budget_relationship", "NONE"))
        )
        budget_minimum_paise = (
            understanding.budget.minimum_inr * 100
            if understanding is not None
            and understanding.budget.minimum_inr is not None
            else _context_int(self._conversation_context, "budget_minimum_paise")
        )
        budget_maximum_paise = intent.max_price_paise or _context_int(
            self._conversation_context, "budget_maximum_paise"
        )
        session_state = AgentSessionState(
            turn_action=_session_turn_action(state.get("turn_action")),
            hard_requirements=state.get(
                "hard_requirements",
                _context_strings(self._conversation_context, "hard_requirements"),
            ),
            soft_preferences=state.get(
                "soft_preferences",
                _context_strings(self._conversation_context, "soft_preferences"),
            ),
            excluded_terms=state.get(
                "excluded_terms",
                _context_strings(self._conversation_context, "excluded_terms"),
            ),
            excluded_product_ids=state.get(
                "excluded_product_ids",
                context_uuid_list(self._conversation_context, "excluded_product_ids"),
            ),
            preferred_brands=state.get(
                "preferred_brands",
                _context_strings(self._conversation_context, "preferred_brands"),
            ),
            budget_relationship=budget_relationship,
            budget_minimum_paise=budget_minimum_paise,
            budget_maximum_paise=budget_maximum_paise,
            requested_count=requested_count,
            exact_requested_product=state.get("exact_requested_product"),
            unavailable_product=unavailable,
            required_brand=state.get("required_brand"),
            evidence_limited=bool(state.get("evidence_limited")),
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
                session_state=session_state,
            )
        )


def _infer_category_from_message(
    message: str, taxonomy: list[CatalogCategoryDescriptor]
) -> str | None:
    text = " ".join(_normalized_words(message))
    manual_aliases = {
        "smartphones": {"phone", "phones", "smartphone", "smartphones", "mobile"},
        "headphones": {"headphone", "headphones", "earbuds", "earphone", "earphones"},
        "laptops": {"laptop", "laptops", "notebook", "macbook"},
        "speakers": {"speaker", "speakers"},
        "tablets": {"tablet", "tablets", "ipad"},
    }
    for category in taxonomy:
        aliases = {
            category.slug.replace("-", " ").casefold(),
            category.display_name.casefold(),
            *(alias.casefold() for alias in category.aliases),
            *manual_aliases.get(category.slug, set()),
        }
        if any(
            f" {alias} " in f" {text} "
            for alias in aliases
            if alias
        ):
            return category.slug
    return None


def _parse_max_price_paise(message: str) -> int | None:
    text = message.casefold().replace(",", "")
    match = re.search(
        r"\b(?:under|below|maximum|max(?:imum)? budget|up to|within)\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(k|lakh|lakhs|lac|lacs)?\b",
        text,
    )
    if match is None:
        return None
    amount = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        amount *= 1_000
    elif suffix in {"lakh", "lakhs", "lac", "lacs"}:
        amount *= 100_000
    rupees = int(amount)
    return rupees * 100 if rupees > 0 else None


def _context_int(context: dict[str, object], key: str) -> int | None:
    value = context.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _context_strings(context: dict[str, object], key: str) -> list[str]:
    value = context.get(key)
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = " ".join(item.split()).strip().casefold()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def _merge_strings(first: list[str], second: list[str]) -> list[str]:
    result: list[str] = []
    for value in [*first, *second]:
        normalized = " ".join(value.split()).strip().casefold()
        if normalized and normalized not in result:
            result.append(normalized[:120])
    return result[:16]


def _merge_uuids(first: list[UUID], second: list[UUID]) -> list[UUID]:
    result: list[UUID] = []
    for value in [*first, *second]:
        if value not in result:
            result.append(value)
    return result[:40]


def _is_continuation_message(value: str) -> bool:
    words = set(_normalized_words(value))
    return bool(
        words.intersection(
            {
                "another",
                "cheaper",
                "current",
                "different",
                "first",
                "instead",
                "it",
                "keep",
                "last",
                "same",
                "second",
                "still",
                "that",
                "third",
                "those",
            }
        )
    ) or "using every requirement" in value.casefold()


def _is_reference_only_query(value: str) -> bool:
    words = set(_normalized_words(value))
    return bool(words) and words.issubset(
        {
            "another",
            "cheaper",
            "cheapest",
            "different",
            "first",
            "it",
            "last",
            "one",
            "option",
            "product",
            "second",
            "that",
            "third",
            "this",
        }
    )


def _canonical_identity(value: str) -> str:
    return " ".join(_normalized_words(value))


def _find_exact_identity(
    requested_name: str, products: list[CatalogProduct]
) -> CatalogProduct | None:
    requested = _canonical_identity(requested_name)
    matches: list[CatalogProduct] = []
    for product in products:
        aliases = {
            _canonical_identity(product.title),
            _canonical_identity(product.model),
            _canonical_identity(f"{product.brand} {product.model}"),
            _canonical_identity(product.sku),
        }
        if requested in aliases:
            matches.append(product)
    return matches[0] if len(matches) == 1 else None


def _preferred_brands(
    requirements: list[str],
    products: list[CatalogProduct],
    previous: list[str],
) -> list[str]:
    result = list(previous)
    text = " ".join(requirements).casefold()
    for brand in dict.fromkeys(product.brand for product in products):
        if brand.casefold() in text and brand.casefold() not in result:
            result.append(brand.casefold())
    return result[:8]


def _product_matches_excluded_terms(
    product: CatalogProduct, excluded_terms: list[str]
) -> bool:
    haystack = _canonical_identity(
        f"{product.brand} {product.model} {product.title}"
    )
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
    for value in excluded_terms:
        words = [word for word in _normalized_words(value) if word not in generic]
        if words and " ".join(words) in haystack:
            return True
    return False


def _should_diversify_hits(
    state: ShoppingGraphState, hits: list[AgentCatalogHit]
) -> bool:
    if state.get("intent_mode") not in {"RECOMMEND", "BUY"}:
        return False
    message = state["request"].message.casefold()
    if any(word in message for word in ("cheapest", "lowest price", "latest", "newest")):
        return False
    criteria = _merge_strings(
        state.get("hard_requirements", []), state.get("soft_preferences", [])
    )
    if not criteria:
        return True
    criterion_words = {
        word
        for criterion in criteria
        for word in _normalized_words(criterion)
        if len(word) > 2
        and word
        not in {
            "available",
            "best",
            "good",
            "phone",
            "product",
            "smartphone",
            "with",
            "for",
            "and",
            "the",
        }
    }
    documents = [
        _canonical_identity(
            " ".join(
                [
                    hit.product.description,
                    *hit.product.search_tags,
                    *(f"{key} {value}" for key, value in hit.product.specifications.items()),
                ]
            )
        )
        for hit in hits
    ]
    return not criterion_words or not any(
        any(word in document for word in criterion_words) for document in documents
    )


def _diverse_hit_ids(hits: list[AgentCatalogHit], limit: int) -> list[UUID]:
    ordered = sorted(
        hits,
        key=lambda hit: (
            hit.product.brand.casefold(),
            hit.product.offer_price_paise,
            hit.product.model.casefold(),
        ),
    )
    selected: list[UUID] = []
    seen_brands: set[str] = set()
    for hit in ordered:
        brand = hit.product.brand.casefold()
        if brand in seen_brands:
            continue
        selected.append(hit.product.id)
        seen_brands.add(brand)
        if len(selected) >= limit:
            return selected
    for hit in ordered:
        if hit.product.id not in selected:
            selected.append(hit.product.id)
        if len(selected) >= limit:
            break
    return selected


def _should_present_unranked(
    state: ShoppingGraphState, shortlist: list[AgentRecommendation]
) -> bool:
    if (
        len(shortlist) <= 1
        or state.get("exact_match")
        or state.get("similarity_fallback")
        or state.get("intent_mode") == "COMPARE"
    ):
        return False
    message = state["request"].message.casefold()
    if any(word in message for word in ("cheapest", "lowest price", "latest", "newest")):
        return False
    criteria = _merge_strings(
        state.get("hard_requirements", []), state.get("soft_preferences", [])
    )
    generic = {
        "available",
        "best",
        "good",
        "in stock",
        "phone",
        "product",
        "smartphone",
        "under budget",
    }
    criteria = [criterion for criterion in criteria if criterion not in generic]
    feature_rich = all(
        sum(
            1
            for key, value in item.product.specifications.items()
            if key != "product_type" and value not in (None, "", [])
        )
        >= 2
        for item in shortlist
    )
    if not criteria:
        return not feature_rich
    evidence_documents = [
        _canonical_identity(
            " ".join(
                [
                    item.product.description,
                    *item.product.search_tags,
                    *(
                        f"{key} {value}"
                        for key, value in item.product.specifications.items()
                    ),
                ]
            )
        )
        for item in shortlist
    ]
    criterion_words = {
        word
        for criterion in criteria
        for word in _normalized_words(criterion)
        if len(word) > 2 and word not in {"with", "for", "and", "the"}
    }
    return not criterion_words or not any(
        any(word in document for word in criterion_words)
        for document in evidence_documents
    )


def _deterministic_decision(
    shortlist: list[AgentRecommendation], selected_product_id: UUID, reason: str
) -> AgentProductDecision:
    return AgentProductDecision(
        selected_product_id=selected_product_id,
        ranked_product_ids=[item.product.id for item in shortlist],
        winner_reason=reason,
        tradeoffs=[],
        upsell_product_id=None,
        upsell_reason=None,
        cross_sell_product_id=None,
        cross_sell_reason=None,
        decision_source="deterministic",
    )


def _comparison_fact_pairs(
    product: CatalogProduct,
    request_text: str,
    *,
    limit: int,
) -> list[tuple[str, str]]:
    criterion_groups = (
        ({"camera", "photo", "photography", "video"}, ("rear_cameras", "camera_features", "front_cameras")),
        ({"battery", "backup", "endurance"}, ("battery_capacity_mah", "battery_life_hours", "battery_claim")),
        ({"charge", "charging", "charger"}, ("wired_charging_w", "charging_case")),
        ({"gaming", "performance", "fast", "processor"}, ("processor", "processor_family", "refresh_rate_hz", "gaming_features", "graphics")),
        ({"display", "screen"}, ("display", "display_size_inches", "main_display", "cover_display", "refresh_rate_hz")),
        ({"light", "lightweight", "weight", "compact", "portable"}, ("weight_g", "form_factor", "portability")),
        ({"noise", "anc", "cancellation"}, ("active_noise_cancellation", "sound_modes")),
        ({"water", "dust", "durable", "durability"}, ("water_resistance", "water_dust_resistance")),
        ({"pen", "stylus", "drawing", "notes"}, ("stylus_support", "included_input")),
    )
    defaults = {
        "smartphones": (
            "form_factor", "processor", "display", "refresh_rate_hz", "rear_cameras",
            "battery_capacity_mah", "battery_claim", "wired_charging_w", "weight_g",
        ),
        "speakers": (
            "form_factor", "audio_output", "battery_life_hours",
            "water_dust_resistance", "connectivity", "extra_features",
        ),
        "headphones": (
            "form_factor", "active_noise_cancellation", "battery_life_hours",
            "battery_claim", "audio_codecs", "water_dust_resistance", "weight_g",
        ),
        "laptops": (
            "form_factor", "display_size_inches", "processor_family", "platform",
            "graphics", "features", "configuration_note",
        ),
        "tablets": (
            "form_factor", "display_size_inches", "display", "processor",
            "refresh_rate_hz", "battery_capacity_mah", "stylus_support", "platform",
        ),
    }
    request_words = set(_normalized_words(request_text))
    ordered_keys: list[str] = []
    for words, keys in criterion_groups:
        if request_words.intersection(words):
            ordered_keys.extend(keys)
    ordered_keys.extend(defaults.get(product.category, ()))
    ordered_keys.extend(sorted(product.specifications))

    labels = {
        "rear_cameras": "rear cameras",
        "front_cameras": "front cameras",
        "camera_features": "camera features",
        "battery_capacity_mah": "battery",
        "battery_life_hours": "battery life",
        "battery_claim": "battery claim",
        "wired_charging_w": "wired charging",
        "refresh_rate_hz": "refresh rate",
        "weight_g": "weight",
        "display_size_inches": "display size",
        "active_noise_cancellation": "active noise cancellation",
        "water_dust_resistance": "water/dust resistance",
        "water_resistance": "water resistance",
        "processor_family": "processor family",
        "configuration_note": "configuration",
    }
    suffixes = {
        "battery_capacity_mah": " mAh",
        "battery_life_hours": " hours",
        "wired_charging_w": " W",
        "refresh_rate_hz": " Hz",
        "weight_g": " g",
        "display_size_inches": " inches",
    }
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ordered_keys:
        if key in seen or key == "product_type":
            continue
        value = product.specifications.get(key)
        if value is None or value == "" or value == []:
            continue
        seen.add(key)
        if isinstance(value, bool):
            rendered = "yes" if value else "no"
        elif isinstance(value, list):
            rendered = ", ".join(str(item) for item in value[:4])
        else:
            rendered = f"{value}{suffixes.get(key, '')}"
        label = labels.get(key, key.replace("_", " "))
        selected.append((label, rendered))
        if len(selected) >= limit:
            break
    return selected


def _grounded_comparison_reply(
    recommendations: list[AgentRecommendation],
    winner: AgentRecommendation | None,
    request_text: str,
) -> str:
    compared: list[str] = []
    for item in recommendations[:4]:
        facts = _comparison_fact_pairs(item.product, request_text, limit=4)
        details = "; ".join(f"{label}: {value}" for label, value in facts)
        compared.append(
            f"{item.product.title} ({_format_inr(item.product.offer_price_paise)})"
            + (f" — {details}" if details else "")
        )
    reply = "Verified comparison: " + " | ".join(compared) + "."
    if winner is not None:
        reply += (
            f" Based on your stated preference, the comparison selected "
            f"{winner.product.title}. No checkout was prepared."
        )
    else:
        reply += " No checkout was prepared; tell me which option you prefer."
    return reply


def _grounded_winner_reason(
    winner: AgentRecommendation,
    shortlist: list[AgentRecommendation],
    state: ShoppingGraphState,
) -> str:
    product = winner.product
    facts = [
        f"{product.title} is verified in stock at {_format_inr(product.offer_price_paise)}",
        f"and is listed in the {product.category} category",
    ]
    maximum = state["intent"].max_price_paise
    if maximum is not None:
        facts.append(f"within the {_format_inr(maximum)} maximum")
    evidence = _comparison_fact_pairs(product, state["request"].message, limit=3)
    if evidence:
        facts.append(
            "with verified "
            + "; ".join(f"{label}: {value}" for label, value in evidence)
        )
    elif len(shortlist) > 1:
        facts.append(
            "using only verified catalogue identity, price, stock, tags, and listed specifications"
        )
    return ", ".join(facts) + "."


def _session_turn_action(value: object) -> str:
    allowed = {"BUY", "RECOMMEND", "COMPARE", "CANCEL", "MEMORY", "OTHER"}
    normalized = str(value or "OTHER").upper()
    return normalized if normalized in allowed else "OTHER"


def _normalized_words(value: str) -> list[str]:
    normalized = "".join(
        character if character.isalnum() else " "
        for character in value.casefold()
    )
    return normalized.split()


def _requests_latest_available(value: str) -> bool:
    words = _normalized_words(value)
    return "latest" in words or "newest" in words or (
        "most" in words and "recent" in words
    )


def _is_latest_only_followup(value: str) -> bool:
    words = set(_normalized_words(value))
    return bool(words) and _requests_latest_available(value) and words.issubset(
        {
            "the",
            "latest",
            "newest",
            "most",
            "recent",
            "available",
            "one",
            "model",
            "please",
        }
    )


def _without_generation_tokens(value: str) -> str:
    return " ".join(
        word for word in value.split() if not any(character.isdigit() for character in word)
    ).strip()


def _repeats_recent_clarification(
    question: str,
    conversation_context: dict[str, object],
) -> bool:
    normalized_question = _normalized_words(question)
    recent_turns = conversation_context.get("recent_turns")
    if not isinstance(recent_turns, list):
        return False
    for turn in recent_turns[-4:]:
        if not isinstance(turn, dict):
            continue
        assistant = turn.get("assistant")
        if isinstance(assistant, str) and _normalized_words(assistant) == normalized_question:
            return True
    return False


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
        "excluded_terms": state.get("excluded_terms", []),
        "required_brand": state.get("required_brand"),
        "requested_count": state.get("requested_count"),
        "unavailable_product": state.get("unavailable_product_name"),
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
