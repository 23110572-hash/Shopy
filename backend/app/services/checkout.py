"""Idempotent Standard Checkout orchestration and provider reconciliation."""

import hmac
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database import Database
from app.domain.purchase_state import PurchaseState, ensure_transition
from app.gateways.razorpay import (
    ProviderOrder,
    ProviderPayment,
    RazorpayGatewayError,
    RazorpayRejectedError,
    RazorpayStandardCheckoutGateway,
)
from app.models.account import ShoppingAgentControls
from app.models.agent_order import AgentFulfillmentOrder, AgentFulfillmentStatus
from app.models.commerce import (
    PaymentAttempt,
    PaymentStatus,
    ProviderOrderOperationState,
    PurchaseQuote,
    PurchaseReservation,
    RazorpayOrder,
    ReservationStatus,
    WebhookEvent,
    WebhookProcessingStatus,
)
from app.models.merchant import Merchant
from app.models.orders import DeliveryAddress
from app.models.purchase_run import PurchaseRun
from app.models.user import User
from app.repositories.accounts import AccountRepository
from app.repositories.commerce import CommerceRepository
from app.repositories.orders import DeliveryAddressRepository
from app.repositories.products import ProductRepository
from app.schemas.agent import PurchaseProposal
from app.schemas.catalog import CatalogProduct
from app.schemas.checkout import (
    CheckoutCallbackRequest,
    CheckoutSessionResponse,
    PostPurchaseCrossSellDecisionRequest,
    PostPurchaseCrossSellDecisionResponse,
    PostPurchaseCrossSellOffer,
    PurchaseRunStatusResponse,
    RazorpayWebhookResponse,
)
from app.services.orders import OrderService
from app.services.proposals import (
    ProposalStaleError,
    persist_post_purchase_cross_sell_proposal,
)

RESERVATION_TTL = timedelta(minutes=15)


class CheckoutServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PreparedPostPurchaseCrossSell:
    offer: PostPurchaseCrossSellOffer
    controls: ShoppingAgentControls


@dataclass(frozen=True, slots=True)
class PreparedCheckout:
    run_id: UUID
    proposal_id: UUID
    order_record_id: UUID
    reservation_id: UUID
    fulfillment_order_id: UUID
    fulfillment_order_number: str
    fulfillment_status: str
    shipping_address: dict[str, Any]
    policy_snapshot: dict[str, Any]
    prefill_contact: str
    receipt: str
    amount_paise: int
    currency: str
    merchant_name: str
    description: str
    expires_at: datetime
    provider_order_id: str | None = None


class CheckoutService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._settings = settings

    async def create_order(
        self,
        *,
        buyer: User,
        proposal_id: UUID,
        address_id: UUID,
        idempotency_key: str,
    ) -> CheckoutSessionResponse:
        if not self._settings.razorpay_api_configured:
            raise CheckoutServiceError(
                "RAZORPAY_NOT_CONFIGURED",
                "Razorpay Test Mode API keys are not configured",
                status_code=503,
            )
        prepared = await self._prepare_order(
            buyer=buyer,
            proposal_id=proposal_id,
            address_id=address_id,
            idempotency_key=idempotency_key,
        )
        if prepared.provider_order_id is not None:
            return self._checkout_session(prepared, buyer)

        expected_notes = {
            "shopy_run_id": str(prepared.run_id),
            "shopy_proposal_id": str(prepared.proposal_id),
            "shopy_fulfillment_id": str(prepared.fulfillment_order_id),
        }
        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            provider_order = await gateway.create_order(
                amount_paise=prepared.amount_paise,
                receipt=prepared.receipt,
                notes=expected_notes,
            )
        except RazorpayRejectedError as exc:
            await self._mark_order_failed(prepared, str(exc))
            raise CheckoutServiceError(
                "ORDER_REJECTED",
                "Razorpay rejected the test Order. No payment was initiated.",
                status_code=502,
            ) from exc
        except RazorpayGatewayError as exc:
            await self._mark_order_unknown(
                prepared,
                "Razorpay Order creation returned an ambiguous result; no retry was issued.",
            )
            raise CheckoutServiceError(
                "ORDER_UNKNOWN",
                "Order status is unknown. Do not retry payment; manual reconciliation is required.",
                status_code=409,
            ) from exc
        finally:
            await gateway.aclose()

        if (
            provider_order.amount_paise != prepared.amount_paise
            or provider_order.currency != prepared.currency
            or provider_order.receipt != prepared.receipt
            or provider_order.notes != expected_notes
            or not 1 <= len(provider_order.order_id) <= 64
            or not 1 <= len(provider_order.status) <= 40
            or provider_order.attempts < 0
        ):
            await self._mark_order_unknown(
                prepared,
                "Razorpay returned Order facts that did not match the immutable quote.",
            )
            raise CheckoutServiceError(
                "ORDER_MISMATCH",
                "Razorpay Order validation failed. Do not attempt payment.",
                status_code=502,
            )
        stored = await self._store_provider_order(prepared, provider_order)
        return self._checkout_session(stored, buyer)

    async def get_status(
        self,
        *,
        buyer_user_id: UUID,
        run_id: UUID,
    ) -> PurchaseRunStatusResponse:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(run_id, buyer_user_id=buyer_user_id)
            if run is None:
                raise CheckoutServiceError(
                    "RUN_NOT_FOUND", "Purchase run not found", status_code=404
                )
            quote = await repository.get_quote_for_run(run.id)
            if quote is None:
                raise CheckoutServiceError(
                    "QUOTE_NOT_FOUND", "Purchase quote not found", status_code=404
                )
            order = await repository.get_order_for_run(run.id)
            payment = await repository.get_latest_payment_for_run(run.id)
            reservation = await repository.get_reservation_for_run(run.id)
            fulfillment = await repository.get_fulfillment_for_run(run.id)
            prepared_offer = await self._prepare_post_purchase_cross_sell(
                session=session,
                run=run,
                quote=quote,
                payment=payment,
            )
            return _status_response(
                run=run,
                quote=quote,
                order=order,
                payment=payment,
                reservation=reservation,
                fulfillment=fulfillment,
                razorpay_api_configured=self._settings.razorpay_api_configured,
                post_purchase_offer=(
                    prepared_offer.offer if prepared_offer is not None else None
                ),
                post_purchase_proposal=_recorded_post_purchase_proposal(run),
            )

    async def _prepare_post_purchase_cross_sell(
        self,
        *,
        session: AsyncSession,
        run: PurchaseRun,
        quote: PurchaseQuote,
        payment: PaymentAttempt | None,
    ) -> PreparedPostPurchaseCrossSell | None:
        if not _is_authoritatively_captured(run, payment) or run.terminal_reason is not None:
            return None
        graph_state = dict(run.graph_state)
        proposal_metadata = graph_state.get("proposal_metadata")
        if (
            isinstance(proposal_metadata, dict)
            and proposal_metadata.get("kind") == "POST_PURCHASE_CROSS_SELL"
        ):
            return None
        if isinstance(graph_state.get("post_purchase_cross_sell"), dict):
            return None

        controls = await AccountRepository(session).get_controls(run.buyer_user_id)
        if controls is None or not controls.agent_enabled:
            return None
        price_limits = [
            value
            for value in (
                controls.recommendation_price_ceiling_paise,
                controls.per_purchase_limit_paise,
            )
            if value is not None
        ]
        max_price_paise = min(price_limits) if price_limits else None
        candidate = await ProductRepository(session).find_post_purchase_cross_sell(
            source_product_id=quote.product_id,
            source_merchant_id=quote.merchant_id,
            source_category=quote.category,
            source_brand=quote.brand,
            allowed_categories=controls.category_allowlist,
            max_price_paise=max_price_paise,
        )
        if candidate is None:
            return None

        benefit = " ".join(candidate.benefit.split())[:500]
        if not benefit:
            return None
        product = CatalogProduct.model_validate(candidate.product)
        prompt = (
            f"Would you also like {product.title}? {benefit} "
            "This is optional and would be a separate purchase."
        )[:800]
        return PreparedPostPurchaseCrossSell(
            offer=PostPurchaseCrossSellOffer(
                source_run_id=run.id,
                source_product_title=quote.title,
                product=product,
                product_version=product.version,
                relation_type="POST_PURCHASE_CROSS_SELL",
                benefit=benefit,
                prompt=prompt,
            ),
            controls=controls,
        )

    async def decide_post_purchase_cross_sell(
        self,
        *,
        buyer_user_id: UUID,
        run_id: UUID,
        request: PostPurchaseCrossSellDecisionRequest,
    ) -> PostPurchaseCrossSellDecisionResponse:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(
                run_id,
                buyer_user_id=buyer_user_id,
                for_update=True,
            )
            if run is None:
                raise CheckoutServiceError(
                    "RUN_NOT_FOUND", "Purchase run not found", status_code=404
                )
            quote = await repository.get_quote_for_run(run.id)
            payment = await repository.get_latest_payment_for_run(run.id)
            if quote is None:
                raise CheckoutServiceError(
                    "QUOTE_NOT_FOUND", "Purchase quote not found", status_code=404
                )
            if not _is_authoritatively_captured(run, payment):
                raise CheckoutServiceError(
                    "PAYMENT_NOT_CAPTURED",
                    "An optional add-on can be considered only after verified payment capture.",
                    status_code=409,
                )

            graph_state = dict(run.graph_state)
            recorded = graph_state.get("post_purchase_cross_sell")
            if isinstance(recorded, dict):
                same_decision = recorded.get("decision") == request.decision
                same_product = (
                    recorded.get("product_id") == str(request.product_id)
                    and recorded.get("product_version") == request.product_version
                )
                if not same_decision or not same_product:
                    raise CheckoutServiceError(
                        "CROSS_SELL_ALREADY_DECIDED",
                        "This optional add-on has already been accepted or declined.",
                        status_code=409,
                    )
                stored_proposal = recorded.get("purchase_proposal")
                existing_proposal = (
                    PurchaseProposal.model_validate(stored_proposal)
                    if isinstance(stored_proposal, dict)
                    else None
                )
                return PostPurchaseCrossSellDecisionResponse(
                    source_run_id=run.id,
                    decision=request.decision,
                    purchase_proposal=existing_proposal,
                    message=(
                        "The separate add-on proposal is ready for your confirmation."
                        if existing_proposal is not None
                        else "The optional add-on remains declined."
                    ),
                )

            prepared = await self._prepare_post_purchase_cross_sell(
                session=session,
                run=run,
                quote=quote,
                payment=payment,
            )
            if prepared is None:
                raise CheckoutServiceError(
                    "CROSS_SELL_UNAVAILABLE",
                    "No eligible optional add-on is currently available for this purchase.",
                    status_code=409,
                )
            offer = prepared.offer
            if (
                offer.product.id != request.product_id
                or offer.product_version != request.product_version
            ):
                raise CheckoutServiceError(
                    "CROSS_SELL_CHANGED",
                    "The optional add-on changed. Refresh the purchase status before deciding.",
                    status_code=409,
                )

            proposal: PurchaseProposal | None = None
            if request.decision == "ACCEPT":
                try:
                    proposal = await persist_post_purchase_cross_sell_proposal(
                        session=session,
                        settings=self._settings,
                        buyer_user_id=buyer_user_id,
                        source_run=run,
                        source_quote=quote,
                        product=offer.product,
                        controls=prepared.controls,
                        relation_type=offer.relation_type,
                        benefit=offer.benefit,
                    )
                except ProposalStaleError as exc:
                    raise CheckoutServiceError(
                        "CROSS_SELL_CHANGED",
                        "The optional add-on changed before its proposal was saved.",
                        status_code=409,
                    ) from exc

            recorded = {
                "decision": request.decision,
                "decided_at": datetime.now(UTC).isoformat(),
                "product_id": str(offer.product.id),
                "product_version": offer.product_version,
                "source_product_id": str(quote.product_id),
                "relation_type": offer.relation_type,
                "benefit": offer.benefit,
                "purchase_proposal": (
                    proposal.model_dump(mode="json") if proposal is not None else None
                ),
            }
            graph_state["post_purchase_cross_sell"] = recorded
            run.graph_state = graph_state
            accepted = request.decision == "ACCEPT"
            await repository.append_audit(
                run_id=run.id,
                actor="BUYER",
                action=(
                    "POST_PURCHASE_CROSS_SELL_ACCEPTED"
                    if accepted
                    else "POST_PURCHASE_CROSS_SELL_DECLINED"
                ),
                outcome="ALLOWED" if accepted else "INFO",
                explanation=(
                    "The buyer accepted one catalogue-authored add-on; a separate quote was "
                    "created without starting a provider payment."
                    if accepted
                    else "The buyer declined the optional add-on; it will not be shown again."
                ),
                details={
                    "source_product_id": str(quote.product_id),
                    "offered_product_id": str(offer.product.id),
                    "offered_product_version": offer.product_version,
                    "relation_type": offer.relation_type,
                    "benefit": offer.benefit,
                    "child_run_id": str(proposal.run_id) if proposal is not None else None,
                    "child_proposal_id": (
                        str(proposal.proposal_id) if proposal is not None else None
                    ),
                    "provider_write_started": False,
                },
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()
            return PostPurchaseCrossSellDecisionResponse(
                source_run_id=run.id,
                decision=request.decision,
                purchase_proposal=proposal,
                message=(
                    "The separate add-on proposal is ready. Confirm its address and payment only "
                    "if you want to continue."
                    if accepted
                    else "The optional add-on was declined and will not be shown again."
                ),
            )

    async def confirm_payment(
        self,
        *,
        buyer_user_id: UUID,
        run_id: UUID,
        callback: CheckoutCallbackRequest,
    ) -> PurchaseRunStatusResponse:
        if not self._settings.razorpay_api_configured:
            raise CheckoutServiceError(
                "RAZORPAY_NOT_CONFIGURED",
                "Razorpay Test Mode API keys are not configured",
                status_code=503,
            )
        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            if not gateway.verify_checkout_signature(
                order_id=callback.razorpay_order_id,
                payment_id=callback.razorpay_payment_id,
                signature=callback.razorpay_signature,
            ):
                raise CheckoutServiceError(
                    "INVALID_CHECKOUT_SIGNATURE",
                    "Razorpay Checkout signature verification failed",
                    status_code=400,
                )

            order_record_id = await self._validate_callback_ownership(
                buyer_user_id=buyer_user_id,
                run_id=run_id,
                provider_order_id=callback.razorpay_order_id,
            )
            try:
                payment = await gateway.fetch_payment(payment_id=callback.razorpay_payment_id)
            except RazorpayGatewayError as exc:
                await self._mark_payment_unknown(
                    run_id=run_id,
                    buyer_user_id=buyer_user_id,
                    explanation=(
                        "The signed callback was valid, but current provider payment state "
                        "could not be fetched."
                    ),
                )
                raise CheckoutServiceError(
                    "PAYMENT_UNKNOWN",
                    "Payment status is pending provider reconciliation. Do not retry.",
                    status_code=202,
                ) from exc

            await self._apply_provider_payment(
                run_id=run_id,
                order_record_id=order_record_id,
                payment=payment,
                source="CHECKOUT_CALLBACK",
            )
        finally:
            await gateway.aclose()
        return await self.get_status(buyer_user_id=buyer_user_id, run_id=run_id)

    async def reconcile(
        self,
        *,
        buyer_user_id: UUID,
        run_id: UUID,
    ) -> PurchaseRunStatusResponse:
        if not self._settings.razorpay_api_configured:
            raise CheckoutServiceError(
                "RAZORPAY_NOT_CONFIGURED",
                "Razorpay Test Mode API keys are not configured",
                status_code=503,
            )
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(run_id, buyer_user_id=buyer_user_id)
            order = await repository.get_order_for_run(run_id) if run is not None else None
            if run is None or order is None:
                raise CheckoutServiceError(
                    "RUN_NOT_FOUND", "Purchase run or provider operation not found", status_code=404
                )
            provider_order_id = order.provider_order_id
            order_record_id = order.id

        if provider_order_id is None:
            return await self.get_status(buyer_user_id=buyer_user_id, run_id=run_id)

        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            try:
                payments = await gateway.fetch_order_payments(order_id=provider_order_id)
                provider_order = await gateway.fetch_order(order_id=provider_order_id)
            except RazorpayGatewayError:
                await self._mark_payment_unknown(
                    run_id=run_id,
                    buyer_user_id=buyer_user_id,
                    explanation="Provider reconciliation is temporarily unavailable.",
                )
                return await self.get_status(buyer_user_id=buyer_user_id, run_id=run_id)

            await self._refresh_order_status(order_record_id, provider_order)
            for payment in sorted(
                payments,
                key=lambda item: item.created_at_epoch or 0,
            ):
                await self._apply_provider_payment(
                    run_id=run_id,
                    order_record_id=order_record_id,
                    payment=payment,
                    source="RECONCILIATION",
                )
        finally:
            await gateway.aclose()
        return await self.get_status(buyer_user_id=buyer_user_id, run_id=run_id)

    async def process_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        provider_event_id: str,
    ) -> RazorpayWebhookResponse:
        if not self._settings.razorpay_webhook_configured:
            raise CheckoutServiceError(
                "WEBHOOK_NOT_CONFIGURED",
                "Razorpay webhook secret is not configured",
                status_code=503,
            )
        if not self._settings.razorpay_api_configured:
            raise CheckoutServiceError(
                "RAZORPAY_NOT_CONFIGURED",
                "Razorpay provider reconciliation is not configured",
                status_code=503,
            )
        gateway = RazorpayStandardCheckoutGateway(self._settings)
        try:
            if not gateway.verify_webhook_signature(body=raw_body, signature=signature):
                raise CheckoutServiceError(
                    "INVALID_WEBHOOK_SIGNATURE",
                    "Razorpay webhook signature verification failed",
                    status_code=400,
                )
            event_type, payment_id, order_id, payload_facts = _parse_webhook(raw_body)
            event_state = await self._record_webhook_event(
                provider_event_id=provider_event_id,
                event_type=event_type,
                payment_id=payment_id,
                order_id=order_id,
                raw_body=raw_body,
                payload_facts=payload_facts,
            )
            if event_state in {"duplicate", "ignored"}:
                return RazorpayWebhookResponse(status=event_state)

            local_order = await self._find_local_order(order_id)
            if local_order is None:
                # Cart checkout orders are a separate rail from agent purchase
                # runs. Settle those here so a closed browser still finalises.
                settled = False
                if order_id is not None:
                    settled = await OrderService(
                        self._database, self._settings
                    ).settle_provider_order(order_id)
                await self._finish_webhook(
                    provider_event_id,
                    WebhookProcessingStatus.PROCESSED
                    if settled
                    else WebhookProcessingStatus.IGNORED,
                    error=None,
                )
                return RazorpayWebhookResponse(status="processed" if settled else "ignored")

            try:
                if payment_id is not None:
                    payments = [await gateway.fetch_payment(payment_id=payment_id)]
                elif order_id is not None:
                    payments = await gateway.fetch_order_payments(order_id=order_id)
                else:
                    payments = []
                for payment in sorted(
                    payments,
                    key=lambda item: item.created_at_epoch or 0,
                ):
                    await self._apply_provider_payment(
                        run_id=local_order.purchase_run_id,
                        order_record_id=local_order.id,
                        payment=payment,
                        source=f"WEBHOOK:{event_type}",
                    )
            except RazorpayGatewayError as exc:
                await self._finish_webhook(
                    provider_event_id,
                    WebhookProcessingStatus.FAILED,
                    error="Current provider state could not be fetched",
                )
                raise CheckoutServiceError(
                    "WEBHOOK_RECONCILIATION_FAILED",
                    "Webhook was verified but provider reconciliation failed",
                    status_code=503,
                ) from exc
            except CheckoutServiceError:
                await self._finish_webhook(
                    provider_event_id,
                    WebhookProcessingStatus.FAILED,
                    error="Provider state failed authoritative local validation",
                )
                raise

            await self._finish_webhook(
                provider_event_id,
                WebhookProcessingStatus.PROCESSED,
                error=None,
            )
            return RazorpayWebhookResponse(status="processed")
        finally:
            await gateway.aclose()

    async def _prepare_order(
        self,
        *,
        buyer: User,
        proposal_id: UUID,
        address_id: UUID,
        idempotency_key: str,
    ) -> PreparedCheckout:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            quote = await repository.get_quote(
                proposal_id,
                buyer_user_id=buyer.id,
                for_update=True,
            )
            if quote is None:
                raise CheckoutServiceError(
                    "PROPOSAL_NOT_FOUND", "Purchase proposal not found", status_code=404
                )
            run = await repository.get_run(
                quote.purchase_run_id,
                buyer_user_id=buyer.id,
                for_update=True,
            )
            if run is None:
                raise CheckoutServiceError(
                    "RUN_NOT_FOUND", "Purchase run not found", status_code=404
                )
            existing_order = await repository.get_order_for_run(run.id, for_update=True)
            existing_reservation = await repository.get_reservation_for_run(run.id, for_update=True)
            existing_fulfillment = await repository.get_fulfillment_for_run(
                run.id,
                for_update=True,
            )
            merchant = await session.get(Merchant, quote.merchant_id)
            merchant_name = merchant.name if merchant is not None else "Shopy"
            if existing_order is not None:
                if existing_fulfillment is None:
                    raise CheckoutServiceError(
                        "FULFILLMENT_STATE_MISSING",
                        "The address-bound fulfilment record is missing for this provider operation.",
                        status_code=500,
                    )
                if existing_fulfillment.delivery_address_id != address_id:
                    raise CheckoutServiceError(
                        "CHECKOUT_ADDRESS_MISMATCH",
                        "This proposal was already bound to a different delivery address.",
                        status_code=409,
                    )
                if (
                    existing_order.provider_order_id is not None
                    and existing_order.operation_state == ProviderOrderOperationState.CREATED.value
                    and existing_reservation is not None
                    and existing_reservation.status == ReservationStatus.ACTIVE.value
                    and existing_reservation.expires_at > now
                ):
                    return _prepared_from_existing(
                        run,
                        quote,
                        existing_order,
                        existing_reservation,
                        existing_fulfillment,
                        merchant_name,
                    )
                raise CheckoutServiceError(
                    "ORDER_ALREADY_STARTED",
                    "This proposal already has a provider operation. "
                    "Check or reconcile its status.",
                    status_code=409,
                )
            if existing_fulfillment is not None:
                raise CheckoutServiceError(
                    "FULFILLMENT_STATE_CONFLICT",
                    "A fulfilment record exists without its provider operation.",
                    status_code=500,
                )
            if run.provider_write_started or run.state != PurchaseState.QUOTED:
                raise CheckoutServiceError(
                    "RUN_NOT_CHECKOUT_READY",
                    "This purchase run is not eligible to create another Order",
                    status_code=409,
                )
            if quote.expires_at <= now:
                await self._reject_before_provider(
                    session=session,
                    repository=repository,
                    run=run,
                    reason="The immutable quote expired before checkout creation.",
                    policy_denied=False,
                )
                raise CheckoutServiceError(
                    "QUOTE_EXPIRED", "The purchase quote expired", status_code=409
                )

            address = await DeliveryAddressRepository(session).get_owned(
                address_id,
                user_id=buyer.id,
                for_update=True,
            )
            if address is None:
                raise CheckoutServiceError(
                    "ADDRESS_NOT_FOUND",
                    "Choose a saved delivery address owned by this account.",
                    status_code=404,
                )

            product = await ProductRepository(session).get_for_checkout(quote.product_id)
            controls = await AccountRepository(session).get_controls_for_update(buyer.id)
            if product is None or controls is None:
                await self._reject_before_provider(
                    session=session,
                    repository=repository,
                    run=run,
                    reason="Current product or account policy could not be loaded.",
                    policy_denied=True,
                )
                raise CheckoutServiceError(
                    "POLICY_DENIED", "Current product or policy is unavailable", status_code=409
                )

            stale_reason = _quote_stale_reason(quote, product)
            if stale_reason is not None:
                await self._reject_before_provider(
                    session=session,
                    repository=repository,
                    run=run,
                    reason=stale_reason,
                    policy_denied=False,
                )
                raise CheckoutServiceError("QUOTE_STALE", stale_reason, status_code=409)

            reserved_quantity = await repository.active_reserved_quantity(
                product_id=product.id,
                now=now,
                excluding_run_id=run.id,
            )
            if product.inventory_quantity - reserved_quantity < quote.quantity:
                reason = "Current unreserved inventory is insufficient for this quote."
                await self._reject_before_provider(
                    session=session,
                    repository=repository,
                    run=run,
                    reason=reason,
                    policy_denied=False,
                )
                raise CheckoutServiceError("OUT_OF_STOCK", reason, status_code=409)

            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            month_start = day_start.replace(day=1)
            daily_spend = await repository.captured_spend(buyer_user_id=buyer.id, since=day_start)
            monthly_spend = await repository.captured_spend(
                buyer_user_id=buyer.id, since=month_start
            )
            active_reserved_spend = await repository.active_reserved_spend(
                buyer_user_id=buyer.id,
                now=now,
                excluding_run_id=run.id,
            )
            policy_reason = _policy_denial_reason(
                quote=quote,
                controls=controls,
                daily_spend=daily_spend,
                monthly_spend=monthly_spend,
                active_reserved_spend=active_reserved_spend,
            )
            if policy_reason is not None:
                await self._reject_before_provider(
                    session=session,
                    repository=repository,
                    run=run,
                    reason=policy_reason,
                    policy_denied=True,
                )
                raise CheckoutServiceError("POLICY_DENIED", policy_reason, status_code=409)

            shipping_address = _address_snapshot(address)
            address_snapshot_hash = _canonical_hash(shipping_address)
            policy_snapshot: dict[str, Any] = {
                "controls_version": controls.version,
                "agent_enabled": controls.agent_enabled,
                "category": quote.category,
                "category_allowlist": list(controls.category_allowlist),
                "amount_paise": quote.total_amount_paise,
                "currency": quote.currency,
                "recommendation_ceiling_paise": controls.recommendation_price_ceiling_paise,
                "per_purchase_limit_paise": controls.per_purchase_limit_paise,
                "daily_spend_limit_paise": controls.daily_spend_limit_paise,
                "monthly_spend_limit_paise": controls.monthly_spend_limit_paise,
                "daily_captured_paise": daily_spend,
                "monthly_captured_paise": monthly_spend,
                "active_reserved_paise": active_reserved_spend,
                "product_id": str(product.id),
                "product_version": product.version,
                "address_confirmed": True,
                "payment_method": "RAZORPAY",
            }

            run.state = ensure_transition(
                run.state,
                PurchaseState.QUOTE_VALIDATED,
                provider_write_started=False,
            )
            run.state = ensure_transition(
                run.state,
                PurchaseState.POLICY_APPROVED,
                provider_write_started=False,
            )
            reservation = PurchaseReservation(
                purchase_run_id=run.id,
                quote_id=quote.id,
                product_id=product.id,
                quantity=quote.quantity,
                amount_paise=quote.total_amount_paise,
                currency=quote.currency,
                status=ReservationStatus.ACTIVE.value,
                expires_at=now + RESERVATION_TTL,
            )
            session.add(reservation)
            await session.flush()
            run.state = ensure_transition(
                run.state,
                PurchaseState.RESERVED,
                provider_write_started=False,
            )

            fulfillment = AgentFulfillmentOrder(
                order_number=_agent_order_number(run.id),
                purchase_run_id=run.id,
                quote_id=quote.id,
                buyer_user_id=buyer.id,
                delivery_address_id=address.id,
                shipping_address=shipping_address,
                amount_paise=quote.total_amount_paise,
                currency=quote.currency,
                payment_provider="razorpay",
                status=AgentFulfillmentStatus.PENDING_PAYMENT.value,
                policy_snapshot=policy_snapshot,
            )
            session.add(fulfillment)
            await session.flush()

            receipt = f"shopy_{run.id.hex}"
            operation_hash = _canonical_hash(
                {
                    "run_id": str(run.id),
                    "proposal_id": str(quote.id),
                    "fulfillment_order_id": str(fulfillment.id),
                    "address_id": str(address.id),
                    "address_snapshot_hash": address_snapshot_hash,
                    "amount_paise": quote.total_amount_paise,
                    "currency": quote.currency,
                    "receipt": receipt,
                    "idempotency_key": idempotency_key,
                }
            )
            order = RazorpayOrder(
                purchase_run_id=run.id,
                quote_id=quote.id,
                receipt=receipt,
                amount_paise=quote.total_amount_paise,
                currency=quote.currency,
                operation_state=ProviderOrderOperationState.CREATING.value,
                request_hash=operation_hash,
                provider_notes={
                    "shopy_run_id": str(run.id),
                    "shopy_proposal_id": str(quote.id),
                    "shopy_fulfillment_id": str(fulfillment.id),
                },
            )
            session.add(order)
            run.provider_write_started = True
            await session.flush()
            await repository.append_audit(
                run_id=run.id,
                actor="USER",
                action="POLICY_APPROVED_AND_RESERVED",
                outcome="ALLOWED",
                explanation=(
                    "The signed-in buyer explicitly requested Standard Checkout after all current "
                    "price, stock, category, daily, monthly, and per-purchase limits passed."
                ),
                details={
                    "quote_id": str(quote.id),
                    "product_id": str(product.id),
                    "fulfillment_order_id": str(fulfillment.id),
                    "fulfillment_order_number": fulfillment.order_number,
                    "fulfillment_status": fulfillment.status,
                    "delivery_address_id": str(address.id),
                    "address_snapshot_hash": address_snapshot_hash,
                    "amount_paise": quote.total_amount_paise,
                    "currency": quote.currency,
                    "controls_version": controls.version,
                    "daily_captured_paise": daily_spend,
                    "monthly_captured_paise": monthly_spend,
                    "active_reserved_paise": active_reserved_spend,
                    "approval_threshold_paise": controls.approval_required_above_paise,
                    "provider_write_claimed": True,
                    "payment_method": "RAZORPAY",
                },
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()
            return PreparedCheckout(
                run_id=run.id,
                proposal_id=quote.id,
                order_record_id=order.id,
                reservation_id=reservation.id,
                fulfillment_order_id=fulfillment.id,
                fulfillment_order_number=fulfillment.order_number,
                fulfillment_status=fulfillment.status,
                shipping_address=shipping_address,
                policy_snapshot=policy_snapshot,
                prefill_contact=address.phone,
                receipt=receipt,
                amount_paise=quote.total_amount_paise,
                currency=quote.currency,
                merchant_name=merchant_name,
                description=quote.title,
                expires_at=reservation.expires_at,
            )

    async def _reject_before_provider(
        self,
        *,
        session: Any,
        repository: CommerceRepository,
        run: PurchaseRun,
        reason: str,
        policy_denied: bool,
    ) -> None:
        if policy_denied:
            run.state = ensure_transition(
                run.state,
                PurchaseState.QUOTE_VALIDATED,
                provider_write_started=False,
            )
            run.state = ensure_transition(
                run.state,
                PurchaseState.POLICY_DENIED,
                provider_write_started=False,
            )
            action = "POLICY_DENIED"
        else:
            run.state = ensure_transition(
                run.state,
                PurchaseState.CANDIDATE_REJECTED,
                provider_write_started=False,
            )
            run.state = ensure_transition(
                run.state,
                PurchaseState.NO_ELIGIBLE_PRODUCT,
                provider_write_started=False,
            )
            action = "QUOTE_REJECTED"
        run.terminal_reason = reason
        await repository.append_audit(
            run_id=run.id,
            actor="SYSTEM",
            action=action,
            outcome="DENIED",
            explanation=reason,
            details={"provider_write_started": False},
            signing_secret=_audit_secret(self._settings),
        )
        await session.commit()

    async def _store_provider_order(
        self,
        prepared: PreparedCheckout,
        provider_order: ProviderOrder,
    ) -> PreparedCheckout:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(prepared.run_id, for_update=True)
            order = await repository.get_order_for_run(prepared.run_id, for_update=True)
            fulfillment = await repository.get_fulfillment_for_run(prepared.run_id, for_update=True)
            if run is None or order is None or fulfillment is None:
                raise CheckoutServiceError(
                    "ORDER_STATE_MISSING",
                    "Local Order or fulfilment state is missing after provider creation",
                    status_code=500,
                )
            if order.provider_order_id is not None and order.provider_order_id != provider_order.order_id:
                raise CheckoutServiceError(
                    "DUPLICATE_PROVIDER_ORDER",
                    "A different provider Order is already attached to this run",
                    status_code=409,
                )
            order.provider_order_id = provider_order.order_id
            order.operation_state = ProviderOrderOperationState.CREATED.value
            order.provider_status = provider_order.status
            order.attempts = provider_order.attempts
            fulfillment.status = AgentFulfillmentStatus.PENDING_PAYMENT.value
            fulfillment.failure_reason = None
            if run.state in {PurchaseState.RESERVED, PurchaseState.PAYMENT_UNKNOWN}:
                run.state = ensure_transition(run.state, PurchaseState.ORDER_CREATED, provider_write_started=True)
            await repository.append_audit(
                run_id=run.id,
                actor="RAZORPAY",
                action="ORDER_CREATED",
                outcome="ALLOWED",
                explanation="Razorpay returned a matching test-mode Order.",
                details={
                    "provider_order_id": provider_order.order_id,
                    "fulfillment_order_id": str(fulfillment.id),
                    "fulfillment_status": fulfillment.status,
                    "amount_paise": provider_order.amount_paise,
                    "currency": provider_order.currency,
                    "provider_status": provider_order.status,
                },
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()
        return replace(
            prepared,
            fulfillment_status=AgentFulfillmentStatus.PENDING_PAYMENT.value,
            provider_order_id=provider_order.order_id,
        )

    async def _mark_order_unknown(
        self,
        prepared: PreparedCheckout,
        explanation: str,
    ) -> None:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(prepared.run_id, for_update=True)
            order = await repository.get_order_for_run(prepared.run_id, for_update=True)
            fulfillment = await repository.get_fulfillment_for_run(prepared.run_id, for_update=True)
            if run is None or order is None:
                return
            order.operation_state = ProviderOrderOperationState.UNKNOWN.value
            order.provider_status = None
            if fulfillment is not None and fulfillment.status not in {
                AgentFulfillmentStatus.CONFIRMED.value,
                AgentFulfillmentStatus.FULFILLMENT_REVIEW.value,
            }:
                fulfillment.status = AgentFulfillmentStatus.PAYMENT_UNKNOWN.value
                fulfillment.failure_reason = explanation
            if run.state == PurchaseState.RESERVED:
                run.state = ensure_transition(
                    run.state,
                    PurchaseState.PAYMENT_UNKNOWN,
                    provider_write_started=True,
                )
            run.payment_state = PaymentStatus.UNKNOWN.value
            await repository.append_audit(
                run_id=run.id,
                actor="SYSTEM",
                action="PROVIDER_ORDER_UNKNOWN",
                outcome="ERROR",
                explanation=explanation,
                details={"receipt": prepared.receipt, "automatic_retry": False},
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()

    async def _mark_order_failed(
        self,
        prepared: PreparedCheckout,
        provider_reason: str,
    ) -> None:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(prepared.run_id, for_update=True)
            order = await repository.get_order_for_run(prepared.run_id, for_update=True)
            reservation = await repository.get_reservation_for_run(prepared.run_id, for_update=True)
            fulfillment = await repository.get_fulfillment_for_run(prepared.run_id, for_update=True)
            if run is None or order is None:
                return
            order.operation_state = ProviderOrderOperationState.UNKNOWN.value
            order.provider_status = None
            if fulfillment is not None and fulfillment.status not in {
                AgentFulfillmentStatus.CONFIRMED.value,
                AgentFulfillmentStatus.FULFILLMENT_REVIEW.value,
            }:
                fulfillment.status = AgentFulfillmentStatus.PAYMENT_FAILED.value
                fulfillment.failure_reason = provider_reason[:500]
            if reservation is not None and reservation.status == ReservationStatus.ACTIVE.value:
                reservation.status = ReservationStatus.RELEASED.value
                reservation.released_at = datetime.now(UTC)
            if run.state == PurchaseState.RESERVED:
                run.state = ensure_transition(
                    run.state,
                    PurchaseState.PAYMENT_FAILED,
                    provider_write_started=True,
                )
            run.payment_state = PaymentStatus.FAILED.value
            run.terminal_reason = "Razorpay rejected Order creation before payment."
            await repository.append_audit(
                run_id=run.id,
                actor="RAZORPAY",
                action="ORDER_REJECTED",
                outcome="DENIED",
                explanation="Razorpay rejected Order creation before payment initiation.",
                details={"provider_reason": provider_reason[:500]},
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()

    async def _validate_callback_ownership(
        self,
        *,
        buyer_user_id: UUID,
        run_id: UUID,
        provider_order_id: str,
    ) -> UUID:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(run_id, buyer_user_id=buyer_user_id)
            order = await repository.get_order_for_run(run_id) if run is not None else None
            if (
                run is None
                or order is None
                or order.provider_order_id != provider_order_id
                or order.operation_state != ProviderOrderOperationState.CREATED.value
            ):
                raise CheckoutServiceError(
                    "CALLBACK_ORDER_MISMATCH",
                    "The callback Order does not belong to this buyer and purchase run",
                    status_code=400,
                )
            return order.id

    async def _mark_payment_unknown(
        self,
        *,
        run_id: UUID,
        buyer_user_id: UUID | None,
        explanation: str,
    ) -> None:
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(
                run_id,
                buyer_user_id=buyer_user_id,
                for_update=True,
            )
            if run is None:
                return
            fulfillment = await repository.get_fulfillment_for_run(run.id, for_update=True)
            if run.state != PurchaseState.CAPTURED:
                if fulfillment is not None:
                    fulfillment.status = AgentFulfillmentStatus.PAYMENT_UNKNOWN.value
                    fulfillment.failure_reason = explanation
                if run.state == PurchaseState.ORDER_CREATED:
                    run.state = ensure_transition(
                        run.state,
                        PurchaseState.PAYMENT_INITIATED,
                        provider_write_started=True,
                    )
                if run.state == PurchaseState.PAYMENT_INITIATED:
                    run.state = ensure_transition(
                        run.state,
                        PurchaseState.PAYMENT_UNKNOWN,
                        provider_write_started=True,
                    )
                run.payment_state = PaymentStatus.UNKNOWN.value
            await repository.append_audit(
                run_id=run.id,
                actor="SYSTEM",
                action="PAYMENT_UNKNOWN",
                outcome="ERROR",
                explanation=explanation,
                details={"automatic_new_charge": False},
                signing_secret=_audit_secret(self._settings),
            )
            await session.commit()

    async def _apply_provider_payment(
        self,
        *,
        run_id: UUID,
        order_record_id: UUID,
        payment: ProviderPayment,
        source: str,
    ) -> None:
        now = datetime.now(UTC)
        payment_payload_hash = _canonical_hash(asdict(payment))
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            run = await repository.get_run(run_id, for_update=True)
            order_result = await session.execute(
                select(RazorpayOrder)
                .where(RazorpayOrder.id == order_record_id)
                .with_for_update()
            )
            order = order_result.scalar_one_or_none()
            quote = await repository.get_quote_for_run(run_id)
            reservation = await repository.get_reservation_for_run(run_id, for_update=True)
            fulfillment = await repository.get_fulfillment_for_run(run_id, for_update=True)
            if run is None or order is None or quote is None:
                raise CheckoutServiceError(
                    "PAYMENT_CONTEXT_MISSING",
                    "Payment context is missing",
                    status_code=500,
                )

            expected_order_id = order.provider_order_id
            if (
                not 1 <= len(payment.payment_id) <= 64
                or (payment.method is not None and len(payment.method) > 40)
                or (payment.error_code is not None and len(payment.error_code) > 120)
            ):
                raise CheckoutServiceError(
                    "PROVIDER_PAYMENT_INVALID",
                    "Razorpay returned payment fields outside supported bounds",
                    status_code=502,
                )

            if (
                expected_order_id is None
                or payment.order_id != expected_order_id
                or payment.amount_paise != quote.total_amount_paise
                or payment.currency != quote.currency
            ):
                mismatch_reason = "Provider payment facts did not match the immutable quote."
                if run.state != PurchaseState.CAPTURED:
                    if run.state == PurchaseState.ORDER_CREATED:
                        run.state = ensure_transition(
                            run.state,
                            PurchaseState.PAYMENT_INITIATED,
                            provider_write_started=True,
                        )
                    if run.state == PurchaseState.PAYMENT_INITIATED:
                        run.state = ensure_transition(
                            run.state,
                            PurchaseState.PAYMENT_UNKNOWN,
                            provider_write_started=True,
                        )
                    run.payment_state = PaymentStatus.UNKNOWN.value
                    run.terminal_reason = mismatch_reason
                    if fulfillment is not None:
                        fulfillment.status = AgentFulfillmentStatus.PAYMENT_UNKNOWN.value
                        fulfillment.failure_reason = mismatch_reason
                await repository.append_audit(
                    run_id=run.id,
                    actor="SYSTEM",
                    action="PAYMENT_FACT_MISMATCH",
                    outcome="ERROR",
                    explanation=mismatch_reason,
                    details={
                        "provider_payment_id": payment.payment_id,
                        "source": source,
                    },
                    signing_secret=_audit_secret(self._settings),
                )
                await session.commit()
                return

            normalized_status = _normalize_payment_status(payment)
            existing = await repository.get_payment_by_provider_id(
                payment.payment_id,
                for_update=True,
            )
            if existing is None:
                existing = PaymentAttempt(
                    purchase_run_id=run.id,
                    razorpay_order_id=order.id,
                    provider_payment_id=payment.payment_id,
                    provider_order_id=expected_order_id,
                    amount_paise=payment.amount_paise,
                    currency=payment.currency,
                    status=normalized_status,
                    captured=payment.captured,
                    payment_method=payment.method,
                    error_code=payment.error_code,
                    error_description=payment.error_description,
                    payload_hash=payment_payload_hash,
                    provider_created_at=(
                        datetime.fromtimestamp(payment.created_at_epoch, tz=UTC)
                        if payment.created_at_epoch is not None
                        else None
                    ),
                )
                session.add(existing)
            else:
                if (
                    existing.purchase_run_id != run.id
                    or existing.razorpay_order_id != order.id
                    or existing.provider_order_id != expected_order_id
                    or existing.amount_paise != payment.amount_paise
                    or existing.currency != payment.currency
                ):
                    raise CheckoutServiceError(
                        "PAYMENT_OWNERSHIP_CONFLICT",
                        "A provider payment ID is attached to different commerce facts",
                        status_code=500,
                    )
                if existing.status != PaymentStatus.CAPTURED.value:
                    existing.status = normalized_status
                    existing.captured = payment.captured
                    existing.payment_method = payment.method
                    existing.error_code = payment.error_code
                    existing.error_description = payment.error_description
                    existing.payload_hash = payment_payload_hash
            if normalized_status == PaymentStatus.CAPTURED.value:
                existing.captured_at = existing.captured_at or now

            purchase_already_captured = run.state == PurchaseState.CAPTURED
            if run.state == PurchaseState.ORDER_CREATED:
                run.state = ensure_transition(
                    run.state,
                    PurchaseState.PAYMENT_INITIATED,
                    provider_write_started=True,
                )
            if run.state == PurchaseState.PAYMENT_UNKNOWN and normalized_status in {
                PaymentStatus.CREATED.value,
                PaymentStatus.AUTHORIZED.value,
            }:
                run.state = ensure_transition(
                    run.state,
                    PurchaseState.PAYMENT_INITIATED,
                    provider_write_started=True,
                )

            if normalized_status == PaymentStatus.CAPTURED.value:
                if run.state in {
                    PurchaseState.PAYMENT_INITIATED,
                    PurchaseState.PAYMENT_UNKNOWN,
                    PurchaseState.PAYMENT_FAILED,
                }:
                    run.state = ensure_transition(
                        run.state,
                        PurchaseState.CAPTURED,
                        provider_write_started=True,
                    )
                run.terminal_reason = None
                if (
                    reservation is not None
                    and reservation.status != ReservationStatus.CAPTURED.value
                ):
                    product = await ProductRepository(session).get_for_checkout(
                        reservation.product_id
                    )
                    if product is not None and product.inventory_quantity >= reservation.quantity:
                        product.inventory_quantity -= reservation.quantity
                    else:
                        run.terminal_reason = (
                            "Payment captured but inventory requires manual fulfilment or refund."
                        )
                    reservation.status = ReservationStatus.CAPTURED.value
                    reservation.captured_at = now
                run.payment_state = PaymentStatus.CAPTURED.value
                if fulfillment is not None:
                    fulfillment.paid_at = fulfillment.paid_at or now
                    fulfillment.failure_reason = run.terminal_reason
                    fulfillment.status = (
                        AgentFulfillmentStatus.FULFILLMENT_REVIEW.value
                        if run.terminal_reason is not None
                        else AgentFulfillmentStatus.CONFIRMED.value
                    )
            elif purchase_already_captured:
                pass
            elif normalized_status == PaymentStatus.FAILED.value:
                if run.state in {
                    PurchaseState.PAYMENT_INITIATED,
                    PurchaseState.PAYMENT_UNKNOWN,
                }:
                    run.state = ensure_transition(
                        run.state,
                        PurchaseState.PAYMENT_FAILED,
                        provider_write_started=True,
                    )
                if reservation is not None and reservation.status == ReservationStatus.ACTIVE.value:
                    reservation.status = ReservationStatus.RELEASED.value
                    reservation.released_at = now
                run.payment_state = PaymentStatus.FAILED.value
                run.terminal_reason = (
                    payment.error_description or "Razorpay reported payment failure."
                )
                if fulfillment is not None:
                    fulfillment.status = AgentFulfillmentStatus.PAYMENT_FAILED.value
                    fulfillment.failure_reason = run.terminal_reason
            elif normalized_status == PaymentStatus.UNKNOWN.value:
                if run.state == PurchaseState.PAYMENT_INITIATED:
                    run.state = ensure_transition(
                        run.state,
                        PurchaseState.PAYMENT_UNKNOWN,
                        provider_write_started=True,
                    )
                run.payment_state = PaymentStatus.UNKNOWN.value
                if fulfillment is not None:
                    fulfillment.status = AgentFulfillmentStatus.PAYMENT_UNKNOWN.value
                    fulfillment.failure_reason = "Provider payment state requires reconciliation."
            else:
                run.payment_state = normalized_status
                if fulfillment is not None and fulfillment.status not in {
                    AgentFulfillmentStatus.CONFIRMED.value,
                    AgentFulfillmentStatus.FULFILLMENT_REVIEW.value,
                }:
                    fulfillment.status = AgentFulfillmentStatus.PENDING_PAYMENT.value

            await repository.append_audit(
                run_id=run.id,
                actor="RAZORPAY",
                action=f"PAYMENT_{normalized_status}",
                outcome=(
                    "ALLOWED"
                    if normalized_status == PaymentStatus.CAPTURED.value
                    else "DENIED"
                    if normalized_status == PaymentStatus.FAILED.value
                    else "INFO"
                ),
                explanation=(
                    "Current Razorpay payment state was fetched after signature verification or "
                    "a signed webhook."
                ),
                details={
                    "provider_payment_id": payment.payment_id,
                    "provider_order_id": payment.order_id,
                    "status": normalized_status,
                    "captured": payment.captured,
                    "amount_paise": payment.amount_paise,
                    "currency": payment.currency,
                    "source": source,
                },
                signing_secret=_audit_secret(self._settings),
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                async with self._database.session() as verification_session:
                    winner = await CommerceRepository(
                        verification_session
                    ).get_payment_by_provider_id(payment.payment_id)
                if (
                    winner is not None
                    and winner.purchase_run_id == run_id
                    and winner.razorpay_order_id == order_record_id
                    and winner.provider_order_id == expected_order_id
                    and winner.amount_paise == payment.amount_paise
                    and winner.currency == payment.currency
                    and winner.status == normalized_status
                    and winner.captured == payment.captured
                    and hmac.compare_digest(winner.payload_hash, payment_payload_hash)
                ):
                    return
                raise CheckoutServiceError(
                    "PAYMENT_PERSISTENCE_FAILED",
                    "Verified provider payment state could not be persisted",
                    status_code=500,
                ) from exc

    async def _refresh_order_status(
        self,
        order_record_id: UUID,
        provider_order: ProviderOrder,
    ) -> None:
        async with self._database.session() as session:
            result = await session.execute(
                select(RazorpayOrder)
                .where(RazorpayOrder.id == order_record_id)
                .with_for_update()
            )
            order = result.scalar_one_or_none()
            if order is None:
                raise CheckoutServiceError(
                    "ORDER_STATE_MISSING",
                    "Local Order state is missing during reconciliation",
                    status_code=500,
                )
            if (
                order.provider_order_id != provider_order.order_id
                or order.amount_paise != provider_order.amount_paise
                or order.currency != provider_order.currency
                or order.receipt != provider_order.receipt
                or order.provider_notes != provider_order.notes
                or not 1 <= len(provider_order.status) <= 40
                or provider_order.attempts < 0
            ):
                raise CheckoutServiceError(
                    "ORDER_RECONCILIATION_MISMATCH",
                    "Razorpay Order facts did not match the immutable local operation",
                    status_code=502,
                )
            order.provider_status = provider_order.status
            order.attempts = provider_order.attempts
            await session.commit()

    async def _record_webhook_event(
        self,
        *,
        provider_event_id: str,
        event_type: str,
        payment_id: str | None,
        order_id: str | None,
        raw_body: bytes,
        payload_facts: dict[str, Any],
    ) -> str:
        incoming_hash = sha256(raw_body).hexdigest()
        async with self._database.session() as session:
            repository = CommerceRepository(session)
            existing = await repository.get_webhook_event(
                provider_event_id,
                for_update=True,
            )
            if existing is not None:
                if not hmac.compare_digest(existing.payload_hash, incoming_hash):
                    raise CheckoutServiceError(
                        "WEBHOOK_EVENT_ID_COLLISION",
                        "Razorpay event ID was reused with a different signed body",
                        status_code=409,
                    )
                if existing.processing_status == WebhookProcessingStatus.FAILED.value:
                    existing.processing_status = WebhookProcessingStatus.RECEIVED.value
                    existing.processing_error = None
                    existing.processed_at = None
                    await session.commit()
                    return "received"
                return "duplicate"
            local_order = (
                await repository.get_order_by_provider_id(order_id)
                if order_id is not None
                else None
            )
            event = WebhookEvent(
                provider_event_id=provider_event_id,
                event_type=event_type,
                purchase_run_id=local_order.purchase_run_id if local_order else None,
                provider_order_id=order_id,
                provider_payment_id=payment_id,
                payload_hash=incoming_hash,
                payload_facts=payload_facts,
                processing_status=WebhookProcessingStatus.RECEIVED.value,
            )
            session.add(event)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                winner = await repository.get_webhook_event(provider_event_id)
                if winner is None:
                    raise CheckoutServiceError(
                        "WEBHOOK_PERSISTENCE_FAILED",
                        "Verified webhook metadata could not be persisted",
                        status_code=500,
                    ) from exc
                if not hmac.compare_digest(winner.payload_hash, incoming_hash):
                    raise CheckoutServiceError(
                        "WEBHOOK_EVENT_ID_COLLISION",
                        "Razorpay event ID was reused with a different signed body",
                        status_code=409,
                    ) from exc
                return "duplicate"
            return "received"

    async def _finish_webhook(
        self,
        provider_event_id: str,
        status: WebhookProcessingStatus,
        *,
        error: str | None,
    ) -> None:
        async with self._database.session() as session:
            event = await CommerceRepository(session).get_webhook_event(provider_event_id)
            if event is None:
                return
            event.processing_status = status.value
            event.processing_error = error
            event.processed_at = datetime.now(UTC)
            await session.commit()

    async def _find_local_order(self, provider_order_id: str | None) -> RazorpayOrder | None:
        if provider_order_id is None:
            return None
        async with self._database.session() as session:
            return await CommerceRepository(session).get_order_by_provider_id(provider_order_id)

    def _checkout_session(
        self,
        prepared: PreparedCheckout,
        buyer: User,
    ) -> CheckoutSessionResponse:
        if prepared.provider_order_id is None:
            raise CheckoutServiceError(
                "ORDER_NOT_READY", "Razorpay Order is not ready", status_code=409
            )
        key_id, _ = self._settings.require_razorpay_api()
        return CheckoutSessionResponse(
            run_id=prepared.run_id,
            proposal_id=prepared.proposal_id,
            fulfillment_order_id=prepared.fulfillment_order_id,
            fulfillment_order_number=prepared.fulfillment_order_number,
            fulfillment_status=prepared.fulfillment_status,
            shipping_address=prepared.shipping_address,
            policy_snapshot=prepared.policy_snapshot,
            key_id=key_id,
            order_id=prepared.provider_order_id,
            amount_paise=prepared.amount_paise,
            currency="INR",
            merchant_name=prepared.merchant_name,
            description=prepared.description,
            prefill_name=buyer.display_name,
            prefill_email=buyer.email,
            prefill_contact=prepared.prefill_contact,
            expires_at=prepared.expires_at,
        )


def _prepared_from_existing(
    run: PurchaseRun,
    quote: PurchaseQuote,
    order: RazorpayOrder,
    reservation: PurchaseReservation,
    fulfillment: AgentFulfillmentOrder,
    merchant_name: str,
) -> PreparedCheckout:
    shipping_address = dict(fulfillment.shipping_address)
    return PreparedCheckout(
        run_id=run.id,
        proposal_id=quote.id,
        order_record_id=order.id,
        reservation_id=reservation.id,
        fulfillment_order_id=fulfillment.id,
        fulfillment_order_number=fulfillment.order_number,
        fulfillment_status=fulfillment.status,
        shipping_address=shipping_address,
        policy_snapshot=dict(fulfillment.policy_snapshot),
        prefill_contact=str(shipping_address.get("phone") or ""),
        receipt=order.receipt,
        amount_paise=order.amount_paise,
        currency=order.currency,
        merchant_name=merchant_name,
        description=quote.title,
        expires_at=reservation.expires_at,
        provider_order_id=order.provider_order_id,
    )


def _quote_stale_reason(quote: PurchaseQuote, product: Any) -> str | None:
    if not product.is_active:
        return "The selected product is no longer active."
    if product.inventory_quantity < quote.quantity:
        return "The selected product is no longer in stock."
    if product.version != quote.product_version:
        return "The selected product changed after comparison."
    if product.offer_price_paise != quote.unit_amount_paise:
        return "The selected product price changed after comparison."
    if product.merchant_id != quote.merchant_id:
        return "The selected product merchant no longer matches the quote."
    return None


def _policy_denial_reason(
    *,
    quote: PurchaseQuote,
    controls: Any,
    daily_spend: int,
    monthly_spend: int,
    active_reserved_spend: int,
) -> str | None:
    amount = quote.total_amount_paise
    if not controls.agent_enabled:
        return "The Shopy Agent is disabled in account controls."
    if controls.currency != "INR" or quote.currency != "INR":
        return "Only INR purchases are permitted."
    if (
        controls.recommendation_price_ceiling_paise is not None
        and amount > controls.recommendation_price_ceiling_paise
    ):
        return "The quote exceeds the current recommendation ceiling."
    if controls.per_purchase_limit_paise is not None and amount > controls.per_purchase_limit_paise:
        return "The quote exceeds the current per-purchase limit."
    if (
        controls.daily_spend_limit_paise is not None
        and daily_spend + active_reserved_spend + amount > controls.daily_spend_limit_paise
    ):
        return "The purchase would exceed the daily captured-plus-reserved limit."
    if (
        controls.monthly_spend_limit_paise is not None
        and monthly_spend + active_reserved_spend + amount > controls.monthly_spend_limit_paise
    ):
        return "The purchase would exceed the monthly captured-plus-reserved limit."
    return None


def _normalize_payment_status(payment: ProviderPayment) -> str:
    normalized = payment.status.upper()
    if normalized == PaymentStatus.CAPTURED.value:
        return (
            PaymentStatus.CAPTURED.value
            if payment.captured
            else PaymentStatus.UNKNOWN.value
        )
    if payment.captured and normalized != PaymentStatus.REFUNDED.value:
        return PaymentStatus.UNKNOWN.value
    if normalized in {status.value for status in PaymentStatus}:
        return normalized
    return PaymentStatus.UNKNOWN.value


def _recorded_post_purchase_proposal(run: PurchaseRun) -> PurchaseProposal | None:
    recorded = dict(run.graph_state).get("post_purchase_cross_sell")
    if not isinstance(recorded, dict) or recorded.get("decision") != "ACCEPT":
        return None
    payload = recorded.get("purchase_proposal")
    if not isinstance(payload, dict):
        return None
    try:
        return PurchaseProposal.model_validate(payload)
    except (TypeError, ValueError):
        return None


def _is_authoritatively_captured(
    run: PurchaseRun,
    payment: PaymentAttempt | None,
) -> bool:
    return (
        run.state == PurchaseState.CAPTURED
        and run.payment_state == PaymentStatus.CAPTURED.value
        and payment is not None
        and payment.status == PaymentStatus.CAPTURED.value
        and payment.captured is True
    )


def _status_response(
    *,
    run: PurchaseRun,
    quote: PurchaseQuote,
    order: RazorpayOrder | None,
    payment: PaymentAttempt | None,
    reservation: PurchaseReservation | None,
    fulfillment: AgentFulfillmentOrder | None,
    razorpay_api_configured: bool,
    post_purchase_offer: PostPurchaseCrossSellOffer | None = None,
    post_purchase_proposal: PurchaseProposal | None = None,
) -> PurchaseRunStatusResponse:
    now = datetime.now(UTC)
    state = run.state.value
    if order is not None and order.operation_state in {
        ProviderOrderOperationState.CREATING.value,
        ProviderOrderOperationState.UNKNOWN.value,
    }:
        state = PurchaseState.PAYMENT_UNKNOWN.value

    actions: list[str] = []
    reservation_active = (
        reservation is not None
        and reservation.status == ReservationStatus.ACTIVE.value
        and reservation.expires_at > now
    )
    if order is None and run.state == PurchaseState.QUOTED and razorpay_api_configured:
        actions.append("CREATE_ORDER")
    elif (
        order is not None
        and order.provider_order_id is not None
        and order.operation_state == ProviderOrderOperationState.CREATED.value
        and run.state in {PurchaseState.ORDER_CREATED, PurchaseState.NEEDS_USER_AUTH}
        and reservation_active
    ):
        actions.append("OPEN_CHECKOUT")
    elif (
        order is not None
        and order.provider_order_id is not None
        and order.operation_state == ProviderOrderOperationState.CREATED.value
        and run.state in {PurchaseState.PAYMENT_INITIATED, PurchaseState.PAYMENT_UNKNOWN}
    ):
        actions.append("RECONCILE")

    if run.state == PurchaseState.CAPTURED:
        message = "Razorpay confirmed capture. The purchase is recorded."
    elif run.state == PurchaseState.PAYMENT_FAILED:
        message = "Razorpay reported failure. No fulfilment was recorded."
    elif state == PurchaseState.PAYMENT_UNKNOWN.value:
        if order is not None and order.provider_order_id is not None:
            message = (
                "Provider status is uncertain. Do not start another charge; reconcile this run."
            )
        else:
            message = (
                "Order creation is uncertain and has no provider ID. Do not create another Order; "
                "manual reconciliation is required."
            )
    elif "OPEN_CHECKOUT" in actions:
        message = "The genuine Razorpay Test Mode Order is ready for buyer authentication."
    elif order is None:
        message = "The bounded quote is ready to create a Razorpay Test Mode Order."
    else:
        message = "The purchase is waiting for an authoritative provider state."

    return PurchaseRunStatusResponse(
        run_id=run.id,
        proposal_id=quote.id,
        state=state,
        payment_state=run.payment_state,
        order_id=order.provider_order_id if order else None,
        payment_id=payment.provider_payment_id if payment else None,
        provider_order_status=order.provider_status if order else None,
        amount_paise=quote.total_amount_paise,
        currency="INR",
        terminal_reason=run.terminal_reason,
        allowed_actions=actions,
        quote_expires_at=quote.expires_at,
        updated_at=run.updated_at,
        retry_after_ms=2000 if "RECONCILE" in actions else None,
        message=message,
        fulfillment_order_id=fulfillment.id if fulfillment else None,
        fulfillment_order_number=fulfillment.order_number if fulfillment else None,
        fulfillment_status=fulfillment.status if fulfillment else None,
        shipping_address=dict(fulfillment.shipping_address) if fulfillment else None,
        policy_snapshot=dict(fulfillment.policy_snapshot) if fulfillment else {},
        post_purchase_offer=post_purchase_offer,
        post_purchase_proposal=post_purchase_proposal,
    )


def _parse_webhook(raw_body: bytes) -> tuple[str, str | None, str | None, dict[str, Any]]:
    try:
        payload: object = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CheckoutServiceError(
            "INVALID_WEBHOOK_BODY", "Webhook body is not valid JSON", status_code=400
        ) from exc
    if not isinstance(payload, dict):
        raise CheckoutServiceError(
            "INVALID_WEBHOOK_BODY", "Webhook body must be an object", status_code=400
        )
    event_type = payload.get("event")
    provider_payload = payload.get("payload")
    if not isinstance(event_type, str) or not isinstance(provider_payload, dict):
        raise CheckoutServiceError(
            "INVALID_WEBHOOK_BODY", "Webhook event shape is invalid", status_code=400
        )
    if event_type != event_type.strip() or not 1 <= len(event_type) <= 120:
        raise CheckoutServiceError(
            "INVALID_WEBHOOK_BODY", "Webhook event type is invalid", status_code=400
        )

    payment_wrapper = provider_payload.get("payment")
    order_wrapper = provider_payload.get("order")
    payment_entity = payment_wrapper.get("entity") if isinstance(payment_wrapper, dict) else None
    order_entity = order_wrapper.get("entity") if isinstance(order_wrapper, dict) else None
    payment_id = (
        payment_entity.get("id")
        if isinstance(payment_entity, dict) and isinstance(payment_entity.get("id"), str)
        else None
    )
    order_id = None
    if isinstance(payment_entity, dict) and isinstance(payment_entity.get("order_id"), str):
        order_id = payment_entity["order_id"]
    elif isinstance(order_entity, dict) and isinstance(order_entity.get("id"), str):
        order_id = order_entity["id"]

    for identifier in (payment_id, order_id):
        if identifier is not None and (
            identifier != identifier.strip() or not 1 <= len(identifier) <= 64
        ):
            raise CheckoutServiceError(
                "INVALID_WEBHOOK_BODY",
                "Webhook provider identifier is invalid",
                status_code=400,
            )

    facts: dict[str, Any] = {"event": event_type}
    if isinstance(payment_entity, dict):
        for field in ("id", "order_id", "amount", "currency", "status", "captured", "method"):
            value = payment_entity.get(field)
            if isinstance(value, (str, int, bool)):
                facts[field] = value
    if order_id is not None:
        facts["order_id"] = order_id
    return event_type, payment_id, order_id, facts


def _address_snapshot(address: DeliveryAddress) -> dict[str, str | None]:
    return {
        "full_name": address.full_name,
        "phone": address.phone,
        "line1": address.line1,
        "line2": address.line2,
        "landmark": address.landmark,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
    }


def _agent_order_number(run_id: UUID) -> str:
    return f"SA-{run_id.hex[:20].upper()}"


def _audit_secret(settings: Settings) -> str | None:
    return (
        settings.audit_signing_secret.get_secret_value()
        if settings.audit_signing_secret is not None
        else None
    )


def _canonical_hash(payload: object) -> str:
    canonical = json.dumps(
        payload,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(canonical).hexdigest()
