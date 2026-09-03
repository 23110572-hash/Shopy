"""Persist short-lived, database-authoritative purchase proposals."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.purchase_state import PurchaseState, ensure_transition
from app.models.account import ShoppingAgentControls
from app.models.commerce import PurchaseQuote
from app.models.purchase_run import PurchaseRun
from app.repositories.commerce import CommerceRepository
from app.repositories.products import ProductRepository
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    ProposalHardLimits,
    PurchaseProposal,
)
from app.schemas.catalog import CatalogProduct

PROPOSAL_TTL = timedelta(minutes=10)


class ProposalStaleError(RuntimeError):
    """The selected catalogue snapshot changed before it could be persisted."""


async def persist_purchase_proposal(
    *,
    session: AsyncSession,
    settings: Settings,
    buyer_user_id: UUID,
    request: AgentChatRequest,
    response: AgentChatResponse,
    controls: ShoppingAgentControls,
) -> PurchaseProposal | None:
    winner = response.winner
    decision = response.decision
    if winner is None or decision is None:
        return None

    product = await ProductRepository(session).get_active(winner.product.id)
    if (
        product is None
        or not product.in_stock
        or product.version != winner.product.version
        or product.offer_price_paise != winner.product.offer_price_paise
        or product.category != winner.product.category
    ):
        raise ProposalStaleError("The selected product changed before the quote was saved")

    now = datetime.now(UTC)
    expires_at = now + PROPOSAL_TTL
    run_id = uuid4()
    request_payload = {
        "buyer_user_id": str(buyer_user_id),
        "message": request.message,
        "category": request.category.value if request.category else None,
        "max_price_paise": request.max_price_paise,
        "selected_product_id": str(product.id),
        "product_version": product.version,
        "controls_version": controls.version,
    }
    request_hash = _canonical_hash(request_payload)
    run = PurchaseRun(
        id=run_id,
        buyer_user_id=buyer_user_id,
        merchant_id=product.merchant_id,
        idempotency_key=f"proposal:{run_id.hex}",
        request_hash=request_hash,
        command=request.message,
        state=PurchaseState.RECEIVED,
        max_replans=controls.max_replans,
        graph_state={},
    )
    session.add(run)
    await session.flush()

    transitions = [
        PurchaseState.INTENT_PARSED,
        PurchaseState.SEARCHING,
        PurchaseState.PRODUCT_SELECTED,
        PurchaseState.QUOTED,
    ]
    state_history = [PurchaseState.RECEIVED.value]
    for target in transitions:
        run.state = ensure_transition(
            run.state,
            target,
            provider_write_started=run.provider_write_started,
        )
        state_history.append(target.value)

    current_product = CatalogProduct.model_validate(product)
    product_snapshot = current_product.model_dump(mode="json")
    product_snapshot.pop("image_url", None)
    comparison_snapshot = {
        "decision_source": decision.decision_source,
        "ranked_product_ids": [str(product_id) for product_id in decision.ranked_product_ids],
        "tradeoffs": decision.tradeoffs,
        "upsell_product_id": str(decision.upsell_product_id)
        if decision.upsell_product_id
        else None,
        "cross_sell_product_id": str(decision.cross_sell_product_id)
        if decision.cross_sell_product_id
        else None,
    }
    quote_hash = _canonical_hash(
        {
            "run_id": str(run_id),
            "product": product_snapshot,
            "amount_paise": product.offer_price_paise,
            "quantity": 1,
            "currency": "INR",
            "controls_version": controls.version,
            "selection_source": decision.decision_source,
            "selection_reason": decision.winner_reason,
            "expires_at": expires_at.isoformat(),
        }
    )
    quote = PurchaseQuote(
        purchase_run_id=run.id,
        product_id=product.id,
        merchant_id=product.merchant_id,
        product_version=product.version,
        controls_version=controls.version,
        sku=product.sku,
        title=product.title,
        brand=product.brand,
        model=product.model,
        category=product.category.value,
        unit_amount_paise=product.offer_price_paise,
        quantity=1,
        total_amount_paise=product.offer_price_paise,
        currency="INR",
        selection_source=decision.decision_source,
        selection_reason=decision.winner_reason,
        product_snapshot=product_snapshot,
        comparison_snapshot=comparison_snapshot,
        quote_hash=quote_hash,
        expires_at=expires_at,
    )
    session.add(quote)
    run.graph_state = {
        "state_history": state_history,
        "intent": response.intent.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "quote_hash": quote_hash,
    }
    await session.flush()

    signing_secret = (
        settings.audit_signing_secret.get_secret_value()
        if settings.audit_signing_secret is not None
        else None
    )
    await CommerceRepository(session).append_audit(
        run_id=run.id,
        actor="AGENT",
        action="PRODUCT_SELECTED_AND_QUOTED",
        outcome="ALLOWED",
        explanation=decision.winner_reason,
        details={
            "product_id": str(product.id),
            "product_version": product.version,
            "controls_version": controls.version,
            "amount_paise": product.offer_price_paise,
            "currency": "INR",
            "selection_source": decision.decision_source,
            "quote_hash": quote_hash,
        },
        signing_secret=signing_secret,
    )
    await session.flush()

    checkout_available = settings.razorpay_api_configured
    return PurchaseProposal(
        proposal_id=quote.id,
        run_id=run.id,
        product=current_product,
        amount_paise=quote.total_amount_paise,
        selection_source=decision.decision_source,
        selection_reason=decision.winner_reason,
        product_version=product.version,
        controls_version=controls.version,
        expires_at=expires_at,
        checkout_available=checkout_available,
        blocker=None if checkout_available else "PAYMENT_NOT_CONFIGURED",
        hard_limits=ProposalHardLimits(
            requested_or_effective_ceiling_paise=response.intent.max_price_paise,
            recommendation_ceiling_paise=controls.recommendation_price_ceiling_paise,
            per_purchase_limit_paise=controls.per_purchase_limit_paise,
            daily_spend_limit_paise=controls.daily_spend_limit_paise,
            monthly_spend_limit_paise=controls.monthly_spend_limit_paise,
        ),
    )


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(
        payload,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(canonical).hexdigest()
