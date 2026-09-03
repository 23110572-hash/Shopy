"""Executable purchase-state rules independent of FastAPI and providers."""

from enum import StrEnum


class PurchaseState(StrEnum):
    RECEIVED = "RECEIVED"
    INTENT_PARSED = "INTENT_PARSED"
    SEARCHING = "SEARCHING"
    PRODUCT_SELECTED = "PRODUCT_SELECTED"
    QUOTED = "QUOTED"
    QUOTE_VALIDATED = "QUOTE_VALIDATED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    REPLANNING = "REPLANNING"
    NO_ELIGIBLE_PRODUCT = "NO_ELIGIBLE_PRODUCT"
    POLICY_APPROVED = "POLICY_APPROVED"
    POLICY_DENIED = "POLICY_DENIED"
    RESERVED = "RESERVED"
    ORDER_CREATED = "ORDER_CREATED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    CAPTURED = "CAPTURED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    NEEDS_USER_AUTH = "NEEDS_USER_AUTH"


class InvalidPurchaseTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[PurchaseState, frozenset[PurchaseState]] = {
    PurchaseState.RECEIVED: frozenset({PurchaseState.INTENT_PARSED}),
    PurchaseState.INTENT_PARSED: frozenset({PurchaseState.SEARCHING}),
    PurchaseState.SEARCHING: frozenset(
        {PurchaseState.PRODUCT_SELECTED, PurchaseState.NO_ELIGIBLE_PRODUCT}
    ),
    PurchaseState.PRODUCT_SELECTED: frozenset({PurchaseState.QUOTED}),
    PurchaseState.QUOTED: frozenset(
        {PurchaseState.QUOTE_VALIDATED, PurchaseState.CANDIDATE_REJECTED}
    ),
    PurchaseState.QUOTE_VALIDATED: frozenset(
        {
            PurchaseState.POLICY_APPROVED,
            PurchaseState.POLICY_DENIED,
            PurchaseState.CANDIDATE_REJECTED,
        }
    ),
    PurchaseState.CANDIDATE_REJECTED: frozenset(
        {PurchaseState.REPLANNING, PurchaseState.NO_ELIGIBLE_PRODUCT}
    ),
    PurchaseState.REPLANNING: frozenset({PurchaseState.SEARCHING}),
    PurchaseState.POLICY_APPROVED: frozenset({PurchaseState.RESERVED}),
    PurchaseState.RESERVED: frozenset(
        {
            PurchaseState.ORDER_CREATED,
            PurchaseState.PAYMENT_FAILED,
            PurchaseState.PAYMENT_UNKNOWN,
        }
    ),
    PurchaseState.ORDER_CREATED: frozenset({PurchaseState.PAYMENT_INITIATED}),
    PurchaseState.PAYMENT_INITIATED: frozenset(
        {
            PurchaseState.CAPTURED,
            PurchaseState.PAYMENT_FAILED,
            PurchaseState.PAYMENT_UNKNOWN,
            PurchaseState.NEEDS_USER_AUTH,
        }
    ),
    PurchaseState.PAYMENT_UNKNOWN: frozenset(
        {
            PurchaseState.ORDER_CREATED,
            PurchaseState.PAYMENT_INITIATED,
            PurchaseState.CAPTURED,
            PurchaseState.PAYMENT_FAILED,
        }
    ),
    PurchaseState.PAYMENT_FAILED: frozenset({PurchaseState.CAPTURED}),
}

PRE_PAYMENT_SELECTION_STATES = frozenset(
    {
        PurchaseState.SEARCHING,
        PurchaseState.PRODUCT_SELECTED,
        PurchaseState.QUOTED,
        PurchaseState.QUOTE_VALIDATED,
        PurchaseState.CANDIDATE_REJECTED,
        PurchaseState.REPLANNING,
    }
)

TERMINAL_STATES = frozenset(
    {
        PurchaseState.NO_ELIGIBLE_PRODUCT,
        PurchaseState.POLICY_DENIED,
        PurchaseState.CAPTURED,
        PurchaseState.PAYMENT_FAILED,
        PurchaseState.NEEDS_USER_AUTH,
    }
)


def ensure_transition(
    current: PurchaseState,
    target: PurchaseState,
    *,
    provider_write_started: bool,
) -> PurchaseState:
    """Return the target only when the architecture permits the transition."""

    if provider_write_started and target in PRE_PAYMENT_SELECTION_STATES:
        raise InvalidPurchaseTransition(
            "Candidate selection cannot resume after a provider write has started"
        )
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidPurchaseTransition(f"Illegal transition: {current} -> {target}")
    return target


def is_terminal(state: PurchaseState) -> bool:
    return state in TERMINAL_STATES
