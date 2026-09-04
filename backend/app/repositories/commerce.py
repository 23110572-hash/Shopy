"""Transactional persistence queries for authoritative commerce facts."""

import hmac
import json
from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_order import AgentFulfillmentOrder
from app.models.commerce import (
    AuditEntry,
    PaymentAttempt,
    PaymentStatus,
    ProviderOrderOperationState,
    PurchaseQuote,
    PurchaseReservation,
    RazorpayOrder,
    ReservationStatus,
    WebhookEvent,
)
from app.models.purchase_run import PurchaseRun

ZERO_HASH = "0" * 64


class CommerceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_run(
        self,
        run_id: UUID,
        *,
        buyer_user_id: UUID | None = None,
        for_update: bool = False,
    ) -> PurchaseRun | None:
        statement = select(PurchaseRun).where(PurchaseRun.id == run_id)
        if buyer_user_id is not None:
            statement = statement.where(PurchaseRun.buyer_user_id == buyer_user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_fulfillment_for_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentFulfillmentOrder | None:
        statement = select(AgentFulfillmentOrder).where(
            AgentFulfillmentOrder.purchase_run_id == run_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_run_by_idempotency(
        self,
        *,
        buyer_user_id: UUID,
        idempotency_key: str,
        for_update: bool = False,
    ) -> PurchaseRun | None:
        statement = select(PurchaseRun).where(
            PurchaseRun.buyer_user_id == buyer_user_id,
            PurchaseRun.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_quote(
        self,
        quote_id: UUID,
        *,
        buyer_user_id: UUID | None = None,
        for_update: bool = False,
    ) -> PurchaseQuote | None:
        statement = select(PurchaseQuote).where(PurchaseQuote.id == quote_id)
        if buyer_user_id is not None:
            statement = statement.join(
                PurchaseRun, PurchaseRun.id == PurchaseQuote.purchase_run_id
            ).where(PurchaseRun.buyer_user_id == buyer_user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_quote_for_run(self, run_id: UUID) -> PurchaseQuote | None:
        result = await self._session.execute(
            select(PurchaseQuote).where(PurchaseQuote.purchase_run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_reservation_for_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> PurchaseReservation | None:
        statement = select(PurchaseReservation).where(
            PurchaseReservation.purchase_run_id == run_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_order_for_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> RazorpayOrder | None:
        statement = select(RazorpayOrder).where(RazorpayOrder.purchase_run_id == run_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_order_by_provider_id(
        self,
        provider_order_id: str,
        *,
        for_update: bool = False,
    ) -> RazorpayOrder | None:
        statement = select(RazorpayOrder).where(
            RazorpayOrder.provider_order_id == provider_order_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_payment_by_provider_id(
        self,
        provider_payment_id: str,
        *,
        for_update: bool = False,
    ) -> PaymentAttempt | None:
        statement = select(PaymentAttempt).where(
            PaymentAttempt.provider_payment_id == provider_payment_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_latest_payment_for_run(self, run_id: UUID) -> PaymentAttempt | None:
        result = await self._session.execute(
            select(PaymentAttempt)
            .where(PaymentAttempt.purchase_run_id == run_id)
            .order_by(PaymentAttempt.updated_at.desc(), PaymentAttempt.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_webhook_event(
        self,
        provider_event_id: str,
        *,
        for_update: bool = False,
    ) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(
            WebhookEvent.provider_event_id == provider_event_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def active_reserved_quantity(
        self,
        *,
        product_id: UUID,
        now: datetime,
        excluding_run_id: UUID | None = None,
    ) -> int:
        statement = select(func.coalesce(func.sum(PurchaseReservation.quantity), 0)).where(
            PurchaseReservation.product_id == product_id,
            PurchaseReservation.status == ReservationStatus.ACTIVE.value,
            PurchaseReservation.expires_at > now,
        )
        if excluding_run_id is not None:
            statement = statement.where(
                PurchaseReservation.purchase_run_id != excluding_run_id
            )
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def captured_spend(
        self,
        *,
        buyer_user_id: UUID,
        since: datetime,
    ) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(PaymentAttempt.amount_paise), 0))
            .join(PurchaseRun, PurchaseRun.id == PaymentAttempt.purchase_run_id)
            .where(
                PurchaseRun.buyer_user_id == buyer_user_id,
                PaymentAttempt.status == PaymentStatus.CAPTURED.value,
                PaymentAttempt.updated_at >= since,
            )
        )
        return int(result.scalar_one())

    async def active_reserved_spend(
        self,
        *,
        buyer_user_id: UUID,
        now: datetime,
        excluding_run_id: UUID | None = None,
    ) -> int:
        statement = (
            select(func.coalesce(func.sum(PurchaseReservation.amount_paise), 0))
            .join(PurchaseRun, PurchaseRun.id == PurchaseReservation.purchase_run_id)
            .where(
                PurchaseRun.buyer_user_id == buyer_user_id,
                PurchaseReservation.status == ReservationStatus.ACTIVE.value,
                PurchaseReservation.expires_at > now,
            )
        )
        if excluding_run_id is not None:
            statement = statement.where(PurchaseReservation.purchase_run_id != excluding_run_id)
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def list_orders_for_buyer(
        self,
        buyer_user_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[tuple[RazorpayOrder, PurchaseQuote]]:
        result = await self._session.execute(
            select(RazorpayOrder, PurchaseQuote)
            .join(PurchaseQuote, PurchaseQuote.id == RazorpayOrder.quote_id)
            .join(PurchaseRun, PurchaseRun.id == RazorpayOrder.purchase_run_id)
            .where(
                PurchaseRun.buyer_user_id == buyer_user_id,
                RazorpayOrder.provider_order_id.is_not(None),
                RazorpayOrder.operation_state == ProviderOrderOperationState.CREATED.value,
                RazorpayOrder.provider_status.is_not(None),
            )
            .order_by(RazorpayOrder.created_at.desc())
            .limit(limit)
        )
        return [(order, quote) for order, quote in result.all()]

    async def list_payments_for_buyer(
        self,
        buyer_user_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[tuple[PaymentAttempt, PurchaseQuote]]:
        result = await self._session.execute(
            select(PaymentAttempt, PurchaseQuote)
            .join(RazorpayOrder, RazorpayOrder.id == PaymentAttempt.razorpay_order_id)
            .join(PurchaseQuote, PurchaseQuote.id == RazorpayOrder.quote_id)
            .join(PurchaseRun, PurchaseRun.id == PaymentAttempt.purchase_run_id)
            .where(PurchaseRun.buyer_user_id == buyer_user_id)
            .order_by(PaymentAttempt.created_at.desc())
            .limit(limit)
        )
        return [(payment, quote) for payment, quote in result.all()]

    async def list_audit_entries(
        self,
        *,
        run_id: UUID,
        buyer_user_id: UUID,
    ) -> Sequence[AuditEntry]:
        result = await self._session.execute(
            select(AuditEntry)
            .join(PurchaseRun, PurchaseRun.id == AuditEntry.purchase_run_id)
            .where(
                AuditEntry.purchase_run_id == run_id,
                PurchaseRun.buyer_user_id == buyer_user_id,
            )
            .order_by(AuditEntry.sequence_number)
        )
        return result.scalars().all()

    async def append_audit(
        self,
        *,
        run_id: UUID,
        actor: str,
        action: str,
        outcome: str,
        explanation: str,
        details: dict[str, Any],
        signing_secret: str | None,
    ) -> AuditEntry:
        run_result = await self._session.execute(
            select(PurchaseRun).where(PurchaseRun.id == run_id).with_for_update()
        )
        if run_result.scalar_one_or_none() is None:
            raise LookupError("Purchase run does not exist")

        tail_result = await self._session.execute(
            select(AuditEntry)
            .where(AuditEntry.purchase_run_id == run_id)
            .order_by(AuditEntry.sequence_number.desc())
            .limit(1)
        )
        tail = tail_result.scalar_one_or_none()
        sequence_number = 1 if tail is None else tail.sequence_number + 1
        previous_hash = ZERO_HASH if tail is None else tail.entry_hash
        canonical = json.dumps(
            {
                "action": action,
                "actor": actor,
                "details": details,
                "explanation": explanation,
                "outcome": outcome,
                "previous_hash": previous_hash,
                "purchase_run_id": str(run_id),
                "sequence_number": sequence_number,
            },
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        entry_hash = sha256(canonical).hexdigest()
        signature = (
            hmac.new(signing_secret.encode(), entry_hash.encode(), sha256).hexdigest()
            if signing_secret is not None
            else None
        )
        entry = AuditEntry(
            purchase_run_id=run_id,
            sequence_number=sequence_number,
            actor=actor,
            action=action,
            outcome=outcome,
            explanation=explanation,
            details=details,
            previous_hash=previous_hash,
            entry_hash=entry_hash,
            signature=signature,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
