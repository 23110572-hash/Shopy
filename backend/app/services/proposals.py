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
    AgentPolicyCheck,
    AgentProductDecision,
    AgentRecommendation,
    ProposalHardLimits,
    PurchaseProposal,
    ShoppingIntent,
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
    conversation_id: UUID | None = None,
    conversation_turn_id: UUID | None = None,
    idempotency_key: str | None = None,
    proposal_metadata: dict[str, object] | None = None,
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
        "category": request.category,
        "max_price_paise": request.max_price_paise,
        "selected_product_id": str(product.id),
        "product_version": product.version,
        "controls_version": controls.version,
        "proposal_metadata": proposal_metadata or {},
    }
    request_hash = _canonical_hash(request_payload)
    run = PurchaseRun(
        id=run_id,
        buyer_user_id=buyer_user_id,
        merchant_id=product.merchant_id,
        conversation_id=conversation_id,
        conversation_turn_id=conversation_turn_id,
        idempotency_key=idempotency_key or f"proposal:{run_id.hex}",
        request_hash=request_hash,
        command=request.message,
        state=PurchaseState.RECEIVED,
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
        "proposal_metadata": proposal_metadata or {},
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
            "proposal_metadata": proposal_metadata or {},
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
        category=product.category,
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
        "proposal_metadata": proposal_metadata or {},
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
            "proposal_metadata": proposal_metadata or {},
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
        policy_checks=[
            AgentPolicyCheck(
                code="PRICE_WITHIN_LIMIT",
                outcome="ALLOWED",
                explanation="The quoted price is within the current recommendation and per-purchase limits.",
                observed_paise=product.offer_price_paise,
                limit_paise=response.intent.max_price_paise,
            ),
            AgentPolicyCheck(
                code="EXPLICIT_PAYMENT_CONFIRMATION",
                outcome="ALLOWED",
                explanation="The agent cannot pay by itself; the buyer must confirm in Razorpay.",
            ),
        ],
    )


async def persist_post_purchase_cross_sell_proposal(
    *,
    session: AsyncSession,
    settings: Settings,
    buyer_user_id: UUID,
    source_run: PurchaseRun,
    source_quote: PurchaseQuote,
    product: CatalogProduct,
    controls: ShoppingAgentControls,
    relation_type: str,
    benefit: str,
) -> PurchaseProposal:
    """Create a separate, buyer-confirmed proposal for one accepted add-on."""

    reason = f"Optional add-on after {source_quote.title}: {benefit}"[:800]
    recommendation = AgentRecommendation(
        product=product,
        score=100,
        reasons=[benefit[:500]],
    )
    decision = AgentProductDecision(
        selected_product_id=product.id,
        ranked_product_ids=[product.id],
        winner_reason=reason,
        tradeoffs=["This is a separate optional purchase requiring its own confirmation."],
        upsell_product_id=None,
        upsell_reason=None,
        cross_sell_product_id=None,
        cross_sell_reason=None,
        decision_source="deterministic",
    )
    request = AgentChatRequest(
        message=f"Accept optional add-on {product.title}",
        conversation_id=source_run.conversation_id,
    )
    response = AgentChatResponse(
        reply=reason,
        intent_source="deterministic",
        decision_source="deterministic",
        intent=ShoppingIntent(
            query=product.title,
            category=product.category,
            max_price_paise=product.offer_price_paise,
            preferences=[],
        ),
        recommendations=[recommendation],
        winner=recommendation,
        decision=decision,
        account_controls_applied=True,
        outcome="RECOMMENDATIONS",
        resolution_kind="EXACT_MATCH",
        focus_product_id=product.id,
        exact_match=True,
        evaluated_count=1,
        eligible_count=1,
        intent_mode="BUY",
    )
    metadata: dict[str, object] = {
        "kind": "POST_PURCHASE_CROSS_SELL",
        "source_run_id": str(source_run.id),
        "source_proposal_id": str(source_quote.id),
        "source_product_id": str(source_quote.product_id),
        "relation_type": relation_type,
        "benefit": benefit,
    }
    proposal = await persist_purchase_proposal(
        session=session,
        settings=settings,
        buyer_user_id=buyer_user_id,
        request=request,
        response=response,
        controls=controls,
        conversation_id=source_run.conversation_id,
        conversation_turn_id=source_run.conversation_turn_id,
        idempotency_key=f"cross-sell:{source_run.id.hex}:{product.id.hex}",
        proposal_metadata=metadata,
    )
    if proposal is None:
        raise RuntimeError("Accepted post-purchase add-on did not create a proposal")
    return proposal


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(
        payload,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(canonical).hexdigest()
