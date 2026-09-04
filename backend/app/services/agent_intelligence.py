"""Deterministic, bounded conversation planning before the shopping graph runs."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from uuid import UUID

from app.models.product import Product
from app.schemas.agent import (
    AgentChatRequest,
    AgentClarification,
    ClarificationOption,
    ShoppingIntent,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_BUY_WORDS = {"buy", "buying", "purchase", "get", "want", "need", "order"}
_GENERIC_WORDS = {
    "a", "an", "the", "me", "my", "for", "please", "show", "find", "product",
    "phone", "phones", "smartphone", "smartphones", "laptop", "laptops", "tablet",
    "tablets", "speaker", "speakers", "headphone", "headphones", "under", "below",
    "within", "rupees", "rs", "inr", "one", "some", "option", "options",
}
_ORDINALS = {
    "first": 0,
    "1": 0,
    "1st": 0,
    "second": 1,
    "2": 1,
    "2nd": 1,
    "third": 2,
    "3": 2,
    "3rd": 2,
    "fourth": 3,
    "4": 3,
    "4th": 3,
}


@dataclass(slots=True)
class AgentTurnPlan:
    forced_product_id: UUID | None = None
    excluded_product_ids: list[UUID] = field(default_factory=list)
    inherited_intent: ShoppingIntent | None = None
    exact_match: bool = False
    clarification: AgentClarification | None = None
    cross_sell_allowed: bool = False
    replan_increment: bool = False
    resolution_hint: str = "ALTERNATIVES"


def canonicalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("+", " plus ")
    return " ".join(_TOKEN.findall(normalized))


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def _identity_aliases(product: Product) -> set[str]:
    values = {
        canonicalize(product.sku),
        canonicalize(product.model),
        canonicalize(f"{product.brand} {product.model}"),
        canonicalize(product.title),
    }
    return {value for value in values if value}


def _safe_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _context_ids(context: dict[str, object], key: str) -> list[UUID]:
    value = context.get(key)
    if not isinstance(value, list):
        return []
    identifiers: list[UUID] = []
    for item in value[:8]:
        identifier = _safe_uuid(item)
        if identifier is not None and identifier not in identifiers:
            identifiers.append(identifier)
    return identifiers


def _context_intent(context: dict[str, object]) -> ShoppingIntent | None:
    value = context.get("intent")
    if not isinstance(value, dict):
        return None
    try:
        return ShoppingIntent.model_validate(value)
    except ValueError:
        return None


def _selected_from_options(message: str, option_ids: list[UUID], products: dict[UUID, Product]) -> UUID | None:
    normalized = canonicalize(message)
    tokens = normalized.split()
    for token in tokens:
        index = _ORDINALS.get(token)
        if index is not None and index < len(option_ids):
            return option_ids[index]
    matches: list[UUID] = []
    for product_id in option_ids:
        product = products.get(product_id)
        if product is None:
            continue
        aliases = _identity_aliases(product)
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            matches.append(product_id)
    return matches[0] if len(matches) == 1 else None


def plan_agent_turn(
    *,
    request: AgentChatRequest,
    conversation_context: dict[str, object],
    products: list[Product],
    replan_count: int,
) -> AgentTurnPlan:
    """Resolve only server-verifiable references and product identities.

    The returned plan never grants payment authority. Product, policy, stock and price are
    still revalidated by the graph, proposal service and checkout service.
    """

    by_id = {product.id: product for product in products}
    message = canonicalize(request.message)
    previous_intent = _context_intent(conversation_context)
    last_ids = _context_ids(conversation_context, "last_recommendation_ids")
    pending_ids = _context_ids(conversation_context, "pending_option_ids")
    focus_id = _safe_uuid(conversation_context.get("focus_product_id"))

    allowed_selected_ids = set(last_ids) | set(pending_ids)
    if focus_id is not None:
        allowed_selected_ids.add(focus_id)
    if request.selected_product_id in allowed_selected_ids:
        return AgentTurnPlan(
            forced_product_id=request.selected_product_id,
            inherited_intent=previous_intent,
            exact_match=True,
            resolution_hint="EXACT_MATCH",
        )

    if pending_ids:
        selected = _selected_from_options(message, pending_ids, by_id)
        if selected is not None:
            return AgentTurnPlan(
                forced_product_id=selected,
                inherited_intent=previous_intent,
                exact_match=True,
                resolution_hint="EXACT_MATCH",
            )
        short_answer = len(message.split()) <= 5 and not (_BUY_WORDS & set(message.split()))
        if short_answer:
            options = [
                ClarificationOption(product_id=product_id, label=by_id[product_id].title)
                for product_id in pending_ids
                if product_id in by_id
            ][:4]
            if len(options) >= 2:
                return AgentTurnPlan(
                    inherited_intent=previous_intent,
                    clarification=AgentClarification(
                        question="Which product did you mean?",
                        options=options,
                    ),
                    resolution_hint="CLARIFICATION_REQUIRED",
                )

    if request.cross_sell_consent is True and focus_id in by_id:
        return AgentTurnPlan(
            forced_product_id=focus_id,
            inherited_intent=previous_intent,
            exact_match=True,
            cross_sell_allowed=True,
            resolution_hint="EXACT_MATCH",
        )

    message_tokens = set(message.split())
    if focus_id in by_id and ("cheaper" in message_tokens or "another" in message_tokens):
        if replan_count >= 3:
            return AgentTurnPlan(inherited_intent=previous_intent, resolution_hint="NO_MATCH")
        focus = by_id[focus_id]
        ceiling = focus.offer_price_paise - 1 if "cheaper" in message_tokens else None
        prior_ceiling = previous_intent.max_price_paise if previous_intent else None
        if ceiling is not None and prior_ceiling is not None:
            ceiling = min(ceiling, prior_ceiling)
        return AgentTurnPlan(
            excluded_product_ids=[focus.id],
            inherited_intent=ShoppingIntent(
                query=previous_intent.query if previous_intent else "",
                category=focus.category,
                max_price_paise=ceiling or prior_ceiling,
                preferences=previous_intent.preferences if previous_intent else [],
            ),
            replan_increment=True,
        )

    exact: list[tuple[int, UUID]] = []
    for product in products:
        for alias in _identity_aliases(product):
            if _contains_phrase(message, alias):
                exact.append((len(alias.split()), product.id))
    if exact:
        longest = max(length for length, _ in exact)
        identifiers = list(dict.fromkeys(product_id for length, product_id in exact if length == longest))
        if len(identifiers) == 1:
            return AgentTurnPlan(
                forced_product_id=identifiers[0],
                inherited_intent=previous_intent,
                exact_match=True,
                resolution_hint="EXACT_MATCH",
            )
        options = [
            ClarificationOption(product_id=product_id, label=by_id[product_id].title)
            for product_id in identifiers[:4]
        ]
        return AgentTurnPlan(
            inherited_intent=previous_intent,
            clarification=AgentClarification(
                question="I found more than one exact catalogue identity. Which one do you want?",
                options=options,
            ),
            resolution_hint="CLARIFICATION_REQUIRED",
        )

    meaningful = message_tokens - _BUY_WORDS - _GENERIC_WORDS
    meaningful = {token for token in meaningful if not token.isdigit() and len(token) > 1}
    buying_request = bool(message_tokens & _BUY_WORDS)
    if buying_request and meaningful:
        family_matches = [
            product
            for product in products
            if meaningful.issubset(set(canonicalize(f"{product.brand} {product.model} {product.title}").split()))
        ]
        if 2 <= len(family_matches):
            options = [
                ClarificationOption(product_id=product.id, label=product.title)
                for product in family_matches[:4]
            ]
            return AgentTurnPlan(
                inherited_intent=previous_intent,
                clarification=AgentClarification(
                    question="Which exact model would you like?",
                    options=options,
                ),
                resolution_hint="CLARIFICATION_REQUIRED",
            )

    return AgentTurnPlan(inherited_intent=previous_intent)


def conversation_context_after_response(
    *,
    previous: dict[str, object],
    intent: ShoppingIntent,
    recommendation_ids: list[UUID],
    focus_product_id: UUID | None,
    clarification: AgentClarification | None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "schema_version": 1,
        "intent": intent.model_dump(mode="json"),
        "last_recommendation_ids": [str(product_id) for product_id in recommendation_ids[:8]],
        "focus_product_id": str(focus_product_id) if focus_product_id else None,
        "pending_option_ids": (
            [str(option.product_id) for option in clarification.options]
            if clarification is not None
            else []
        ),
    }
    if previous.get("cross_sell_declined") is True:
        context["cross_sell_declined"] = True
    return context
