"""Deterministic turn safety, reference resolution, and catalogue identity helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.schemas.catalog import CatalogProduct

TurnAction = Literal["BUY", "RECOMMEND", "COMPARE", "CANCEL", "MEMORY", "OTHER"]

_WORD_NUMBER = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}
_ORDINAL = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
    "sixth": 5,
    "6th": 5,
    "seventh": 6,
    "7th": 6,
    "eighth": 7,
    "8th": 7,
}
_CANCEL_PATTERNS = (
    r"\bcancel\b",
    r"\bchanged? my mind\b",
    r"\bdo not buy\b",
    r"\bdon'?t buy\b",
    r"\bdont buy\b",
    r"\bdo not (?:order|purchase|prepare checkout)\b",
    r"\bstop(?: buying| the purchase| checkout|\b)",
)
_DISCOVERY_PATTERNS = (
    r"\brecommend",
    r"\bsuggest",
    r"\bshow(?: me)?\b",
    r"\bonly browsing\b",
    r"\bjust (?:show|recommend)",
    r"\brecommendations? only\b",
    r"\blooking for\b",
)
_BUY_PATTERNS = (
    r"\bbuy me\b",
    r"\bbuy (?:it|this|that|the|one|a|an)\b",
    r"\bi want to buy\b",
    r"\bi(?:'d| would) like to buy\b",
    r"\border (?:it|this|that|the|one|a|an)\b",
    r"\bpurchase (?:it|this|that|the|one|a|an)\b",
    r"\bprepare (?:the )?checkout\b",
    r"\bget me (?:the|this|that|a|an)\b",
)
_NON_COMMITTAL_BUY = (
    r"\bmay buy\b",
    r"\bmight buy\b",
    r"\bmaybe buy\b",
    r"\bconsidering (?:buying|a purchase)\b",
)
_MEMORY_PATTERNS = (
    r"\bwhat (?:budget|maximum budget|brand|requirements?|preferences?)\b",
    r"\bwhat (?:was|were|did) (?:my|i)\b",
    r"\bstate my\b",
    r"\brepeat (?:the )?(?:exact )?(?:products?|names?|options?)\b",
    r"\blist (?:the )?(?:products?|phones?|laptops?|names?|options?) (?:you|we|i)\b",
    r"\bwhat are my current\b",
    r"\bwhich product was (?:first|second|third|fourth)\b",
    r"\bwhat (?:is|was) my codeword\b",
)
_PAYMENT_STATUS_PATTERNS = (
    r"\bi (?:already )?paid\b",
    r"\bconfirm (?:that )?(?:my )?payment\b",
    r"\bpayment (?:was |is )?(?:captured|successful|done)\b",
    r"\bship it now\b",
)
_REFERENCE_WORDS = {
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "last",
    "it",
    "this",
    "that",
    "those",
    "one",
    "cheaper",
    "cheapest",
}
_GENERIC_IDENTITY_WORDS = {
    "a",
    "an",
    "available",
    "best",
    "budget",
    "buy",
    "do",
    "for",
    "have",
    "headphone",
    "headphones",
    "i",
    "in",
    "inr",
    "is",
    "it",
    "laptop",
    "laptops",
    "max",
    "maximum",
    "me",
    "model",
    "new",
    "of",
    "phone",
    "phones",
    "please",
    "product",
    "recommend",
    "rupees",
    "show",
    "smartphone",
    "smartphones",
    "stock",
    "the",
    "to",
    "under",
    "want",
}


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    action: TurnAction | None
    explicit_buy: bool = False
    suppress_purchase: bool = False
    is_refinement: bool = False
    another: bool = False
    no_repeat: bool = False
    requested_count: int | None = None
    payment_status_request: bool = False


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("’", "'").split())


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value) is not None for pattern in patterns)


def classify_turn(message: str) -> TurnPolicy:
    """Classify safety-critical latest-turn semantics without inheriting prior mode."""

    text = normalize_text(message)
    discovery = _matches_any(text, _DISCOVERY_PATTERNS)
    cancellation = _matches_any(text, _CANCEL_PATTERNS)
    non_committal = _matches_any(text, _NON_COMMITTAL_BUY)
    explicit_buy = _matches_any(text, _BUY_PATTERNS) and not cancellation and not non_committal
    another = bool(re.search(r"\b(?:another|different|one more|replace)\b", text))
    no_repeat = another or bool(
        re.search(r"\b(?:do not|don'?t|dont|without) repeat\b|\bnot already (?:shown|listed)\b", text)
    )
    refinement = another or bool(
        re.search(
            r"\b(?:cheaper|same budget|same rules|keep (?:the )?same|instead|still|current requirements?|those|from that list)\b",
            text,
        )
    )
    payment_status = _matches_any(text, _PAYMENT_STATUS_PATTERNS)
    defer_recommendation = bool(
        re.search(r"\b(?:do not|don'?t|dont) recommend (?:anything )?(?:yet|now)\b", text)
    )

    if payment_status:
        action: TurnAction | None = "OTHER"
    elif defer_recommendation:
        action = "OTHER"
    elif cancellation and not discovery:
        action = "CANCEL"
    elif explicit_buy:
        action = "BUY"
    elif re.search(r"\b(?:compare|versus|vs\.?|difference between)\b", text):
        action = "COMPARE"
    elif _matches_any(text, _MEMORY_PATTERNS):
        action = "MEMORY"
    elif discovery or refinement:
        action = "RECOMMEND"
    else:
        action = None

    return TurnPolicy(
        action=action,
        explicit_buy=explicit_buy,
        suppress_purchase=(
            cancellation or non_committal or (discovery and not explicit_buy)
        ),
        is_refinement=refinement,
        another=another,
        no_repeat=no_repeat,
        requested_count=_requested_count(text),
        payment_status_request=payment_status,
    )


def is_trusted_buy_turn(message: str) -> bool:
    policy = classify_turn(message)
    return policy.action == "BUY" and policy.explicit_buy and not policy.suppress_purchase


def _requested_count(text: str) -> int | None:
    category_words = r"(?:products?|options?|phones?|smartphones?|laptops?|headphones?|speakers?|tablets?)"
    digit = re.search(rf"\b(?:exactly\s+)?([1-8])\s+{category_words}\b", text)
    if digit:
        return int(digit.group(1))
    for word, number in _WORD_NUMBER.items():
        if re.search(rf"\b(?:exactly\s+)?{word}\s+{category_words}\b", text):
            return number
    return None


def context_uuid_list(context: dict[str, object], key: str) -> list[UUID]:
    result: list[UUID] = []
    value = context.get(key)
    if not isinstance(value, list):
        return result
    for item in value:
        try:
            identifier = UUID(str(item))
        except (TypeError, ValueError, AttributeError):
            continue
        if identifier not in result:
            result.append(identifier)
    return result


def _context_uuid(context: dict[str, object], key: str) -> UUID | None:
    try:
        return UUID(str(context.get(key)))
    except (TypeError, ValueError, AttributeError):
        return None


def resolve_reference_ids(
    message: str,
    context: dict[str, object],
    products: list[CatalogProduct],
) -> list[UUID]:
    """Resolve ordinals and pronouns against stable ordered candidate sets."""

    text = normalize_text(message)
    if classify_turn(message).another:
        return []
    by_id = {product.id: product for product in products}
    active = [
        identifier
        for identifier in context_uuid_list(context, "active_candidate_ids")
        or context_uuid_list(context, "last_recommendation_ids")
        if identifier in by_id
    ]
    compared = [
        identifier
        for identifier in context_uuid_list(context, "last_compared_ids")
        if identifier in by_id
    ]
    pool = compared if compared and re.search(r"\b(?:those two|of those|these two)\b", text) else active
    pool = _remove_textually_excluded_brands(text, pool, by_id)
    if not pool:
        return []

    referenced: list[UUID] = []
    ordinal_matches: list[tuple[int, int]] = []
    for token, index in _ORDINAL.items():
        for match in re.finditer(rf"\b{re.escape(token)}\b", text):
            ordinal_matches.append((match.start(), index))
    for _, index in sorted(ordinal_matches):
        if index < len(pool) and pool[index] not in referenced:
            referenced.append(pool[index])

    focus = _context_uuid(context, "focus_product_id")
    if (
        re.search(r"\b(?:it|this one|that one|that product|this product)\b", text)
        and focus is not None
        and focus in by_id
        and focus not in referenced
    ):
        referenced.insert(0, focus)
    if re.search(r"\b(?:last (?:phone|product|one)|last one (?:shown|recommended))\b", text):
        if focus is not None and focus in by_id and focus not in referenced:
            referenced = [focus]
        elif pool:
            referenced = [pool[-1]]

    if re.search(r"\b(?:cheaper|cheapest|lowest priced|lowest-priced)\b", text):
        choice_pool = referenced or pool
        if choice_pool:
            return [min(choice_pool, key=lambda identifier: by_id[identifier].offer_price_paise)]
    return referenced


def _remove_textually_excluded_brands(
    text: str,
    identifiers: list[UUID],
    products: dict[UUID, CatalogProduct],
) -> list[UUID]:
    result: list[UUID] = []
    for identifier in identifiers:
        brand = re.escape(products[identifier].brand.casefold())
        excluded = re.search(
            rf"\b(?:not|exclude|excluding|without|no)\s+(?:the\s+)?{brand}(?:\s+phone|\b)",
            text,
        )
        if excluded is None:
            result.append(identifier)
    return result


def safe_memory_reply(
    message: str,
    context: dict[str, object],
    products: list[CatalogProduct],
) -> str | None:
    """Answer factual session-memory questions from persisted structured state."""

    text = normalize_text(message)
    history_query = bool(
        re.search(
            r"\b(?:what|which)\b.*\b(?:show|shown|showed|recommend|recommended)\b"
            r"|\b(?:previous|earlier|before)\b.*\b(?:product|products|phone|phones|option|options)\b",
            text,
        )
    )
    if classify_turn(message).action != "MEMORY" and not history_query:
        return None
    by_id = {product.id: product for product in products}
    active_ids = context_uuid_list(context, "active_candidate_ids") or context_uuid_list(
        context, "last_recommendation_ids"
    )
    active = [by_id[identifier] for identifier in active_ids if identifier in by_id]

    for token, index in _ORDINAL.items():
        if re.search(rf"\b{re.escape(token)}\b", text) and index < len(active):
            product = active[index]
            return f"The {token} product was {product.title} at {_format_inr(product.offer_price_paise)}."
    if history_query or re.search(r"\b(?:repeat|list)\b", text):
        if not active:
            return "I have not shown any products in this conversation yet."
        items = ", ".join(
            f"{index}. {product.title} at {_format_inr(product.offer_price_paise)}"
            for index, product in enumerate(active, 1)
        )
        return f"The current session list is: {items}."

    codeword = context.get("codeword")
    budget = context.get("budget_maximum_paise")
    category = context.get("category")
    preferred = _string_list(context.get("preferred_brands"))
    excluded = _string_list(context.get("excluded_terms"))
    requirements = _string_list(context.get("hard_requirements"))
    parts: list[str] = []
    if isinstance(codeword, str) and codeword:
        parts.append(f"codeword {codeword}")
    if isinstance(budget, int) and budget > 0:
        parts.append(f"maximum budget {_format_inr(budget)}")
    if isinstance(category, str) and category:
        parts.append(f"category {category}")
    if preferred:
        parts.append(f"preferred brand {', '.join(preferred)}")
    if excluded:
        parts.append(f"excluded brand or term {', '.join(excluded)}")
    if requirements:
        parts.append(f"requirements {', '.join(requirements)}")
    return (
        "Your saved session requirements are: " + "; ".join(parts) + "."
        if parts
        else "I do not have saved shopping requirements in this conversation yet."
    )


def safe_payment_status_reply() -> str:
    return (
        "I cannot confirm a payment or shipping state from that message alone. "
        "Please open your Shopy Orders page and select the relevant order; I will only report "
        "the verified Razorpay status attached to that order."
    )


def requested_identity_name(message: str, search_query: str) -> str | None:
    """Return a likely exact named model, excluding category/budget-only searches."""

    query = " ".join(search_query.split()).strip(" .")
    if not query or not re.search(r"\d", query):
        return None
    identity_tokens = _identity_tokens(query)
    query_tokens = set(identity_tokens)
    model_numbers = [int(token) for token in identity_tokens if token.isdigit()]
    meaningful_alpha = {
        token
        for token in identity_tokens
        if token.isalpha() and token not in _GENERIC_IDENTITY_WORDS
    }
    if (
        not query_tokens
        or not meaningful_alpha
        or not any(number < 1_000 for number in model_numbers)
    ):
        return None
    text = normalize_text(message)
    identity_context = bool(
        re.search(
            r"\b(?:do you have|in stock|buy|want|recommend|looking for|available|sell)\b",
            text,
        )
    )
    return query[:180] if identity_context else None


def identity_similarity(requested_name: str, product: CatalogProduct) -> float:
    requested = set(_identity_tokens(requested_name))
    candidate = set(_identity_tokens(f"{product.brand} {product.model} {product.title}"))
    if not requested or not candidate:
        return 0.0
    shared = requested & candidate
    score = (2.0 * len(shared)) / (len(requested) + len(candidate))
    requested_modifiers = requested & {"pro", "plus", "ultra", "max", "mini"}
    candidate_modifiers = candidate & {"pro", "plus", "ultra", "max", "mini"}
    if not requested_modifiers and candidate_modifiers:
        score -= 0.08 * len(candidate_modifiers)
    return score


def infer_requested_brand(requested_name: str, products: list[CatalogProduct]) -> str | None:
    text = normalize_text(requested_name)
    brands = list(dict.fromkeys(product.brand for product in products))
    direct = [brand for brand in brands if normalize_text(brand) in text]
    if direct:
        return max(direct, key=len)
    ranked = sorted(
        products,
        key=lambda product: (
            identity_similarity(requested_name, product),
            -product.offer_price_paise,
        ),
        reverse=True,
    )
    return ranked[0].brand if ranked and identity_similarity(requested_name, ranked[0]) >= 0.2 else None


def extract_excluded_brands(
    message: str, products: list[CatalogProduct]
) -> list[str]:
    """Extract every explicitly excluded known catalogue brand from one turn."""

    text = normalize_text(message)
    cue = re.search(
        r"\b(?:exclude|excluding|without|do not include|don'?t include|do not show|don'?t show|do not want|don'?t want|no)\b",
        text,
    )
    if cue is None:
        return []
    excluded_segment = text[cue.start() :]
    result: list[str] = []
    for brand in dict.fromkeys(product.brand for product in products):
        if re.search(rf"\b{re.escape(normalize_text(brand))}\b", excluded_segment):
            result.append(brand.casefold())
    return result


def family_search_query(requested_name: str, brand: str | None) -> str:
    tokens = [token for token in _identity_tokens(requested_name) if token not in _GENERIC_IDENTITY_WORDS]
    # A terminal generation number is intentionally removed while family tokens such as WH/XM stay.
    if tokens and tokens[-1].isdigit():
        tokens.pop()
    query = " ".join(tokens)
    if brand and normalize_text(brand) not in query:
        query = f"{brand} {query}".strip()
    return query[:240]


def _identity_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z]+|\d+", value.casefold()) if token]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _format_inr(paise: int) -> str:
    rupees, remainder = divmod(paise, 100)
    return f"₹{rupees:,}" if remainder == 0 else f"₹{rupees:,}.{remainder:02d}"
