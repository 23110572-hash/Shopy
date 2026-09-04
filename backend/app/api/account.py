"""Authentication and database-backed Shopy account endpoints."""

import hmac
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Database
from app.dependencies import get_database, get_runtime_settings
from app.models.account import AuthSession, ShoppingAgentControls
from app.models.commerce import AuditEntry, ProviderOrderOperationState
from app.models.user import User, UserRole
from app.repositories.accounts import AccountRepository
from app.repositories.commerce import ZERO_HASH, CommerceRepository
from app.schemas.account import (
    AccountProfile,
    AgentControlsResponse,
    AgentControlsUpdate,
    AuditHistoryItem,
    AuditHistoryResponse,
    AuthMessage,
    AuthResponse,
    LoginRequest,
    OrderHistoryItem,
    OrderHistoryResponse,
    ProfileUpdateRequest,
    SignupRequest,
    TransactionHistoryItem,
    TransactionHistoryResponse,
)
from app.security import (
    SESSION_COOKIE,
    CsrfPrincipalDependency,
    CurrentPrincipalDependency,
    SessionCredentials,
    clear_auth_cookies,
    create_session_credentials,
    digest_token,
    hash_password,
    set_auth_cookies,
    verify_password,
)

router = APIRouter(tags=["account"])
DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def _profile(user: User) -> AccountProfile:
    return AccountProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        email_verified=user.email_verified_at is not None,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _controls_response(controls: ShoppingAgentControls) -> AgentControlsResponse:
    return AgentControlsResponse(
        user_id=controls.user_id,
        agent_enabled=controls.agent_enabled,
        recommendation_price_ceiling_paise=controls.recommendation_price_ceiling_paise,
        per_purchase_limit_paise=controls.per_purchase_limit_paise,
        daily_spend_limit_paise=controls.daily_spend_limit_paise,
        monthly_spend_limit_paise=controls.monthly_spend_limit_paise,
        category_allowlist=controls.category_allowlist,
        max_recommendations=controls.max_recommendations,
        currency="INR",
        version=controls.version,
        updated_at=controls.updated_at,
    )


def _verified_audit_signatures(
    entries: Sequence[AuditEntry],
    settings: Settings,
) -> list[bool]:
    signing_secret = (
        settings.audit_signing_secret.get_secret_value()
        if settings.audit_signing_secret is not None
        else None
    )
    expected_previous_hash = ZERO_HASH
    expected_sequence = 1
    chain_valid = True
    verified: list[bool] = []

    for entry in entries:
        canonical = json.dumps(
            {
                "action": entry.action,
                "actor": entry.actor,
                "details": entry.details,
                "explanation": entry.explanation,
                "outcome": entry.outcome,
                "previous_hash": entry.previous_hash,
                "purchase_run_id": str(entry.purchase_run_id),
                "sequence_number": entry.sequence_number,
            },
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        expected_entry_hash = sha256(canonical).hexdigest()
        chain_valid = (
            chain_valid
            and entry.sequence_number == expected_sequence
            and hmac.compare_digest(entry.previous_hash, expected_previous_hash)
            and hmac.compare_digest(entry.entry_hash, expected_entry_hash)
        )
        signature_valid = False
        if chain_valid and signing_secret is not None and entry.signature is not None:
            expected_signature = hmac.new(
                signing_secret.encode(),
                entry.entry_hash.encode(),
                sha256,
            ).hexdigest()
            signature_valid = hmac.compare_digest(entry.signature, expected_signature)
        verified.append(signature_valid)
        expected_previous_hash = entry.entry_hash
        expected_sequence += 1

    return verified


def _new_session(user_id: UUID) -> tuple[AuthSession, SessionCredentials]:
    credentials = create_session_credentials()
    auth_session = AuthSession(
        user_id=user_id,
        token_digest=digest_token(credentials.session_token),
        csrf_digest=digest_token(credentials.csrf_token),
        expires_at=credentials.expires_at,
        last_seen_at=datetime.now(UTC),
    )
    return auth_session, credentials


@router.post("/api/auth/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    response: Response,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> AuthResponse:
    password_hash = await hash_password(payload.password.get_secret_value())
    async with database.session() as session:
        repository = AccountRepository(session)
        if await repository.get_user_by_email(payload.email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already exists",
            )

        user = User(
            email=payload.email,
            display_name=payload.display_name,
            password_hash=password_hash,
            role=UserRole.BUYER,
        )
        session.add(user)
        try:
            await session.flush()
            controls = ShoppingAgentControls(user_id=user.id)
            auth_session, credentials = _new_session(user.id)
            session.add_all([controls, auth_session])
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Account already exists"
            ) from None
        set_auth_cookies(response, settings, credentials)
        return AuthResponse(profile=_profile(user), message="Your Shopy account is ready")


@router.post("/api/auth/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> AuthResponse:
    async with database.session() as session:
        repository = AccountRepository(session)
        user = await repository.get_user_by_email(payload.email)
        valid = await verify_password(
            payload.password.get_secret_value(), user.password_hash if user is not None else None
        )
        if user is None or not valid or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is incorrect",
            )

        existing_token = request.cookies.get(SESSION_COOKIE)
        if existing_token:
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.token_digest == digest_token(existing_token),
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        user.last_login_at = datetime.now(UTC)
        auth_session, credentials = _new_session(user.id)
        session.add(auth_session)
        await session.commit()
        set_auth_cookies(response, settings, credentials)
        return AuthResponse(profile=_profile(user), message="Welcome back to Shopy")


@router.post("/api/auth/logout", response_model=AuthMessage)
async def logout(
    response: Response,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CsrfPrincipalDependency,
) -> AuthMessage:
    async with database.session() as session:
        await session.execute(
            update(AuthSession)
            .where(AuthSession.id == principal.session.id)
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    clear_auth_cookies(response, settings)
    return AuthMessage(message="You are signed out")


@router.get("/api/account/profile", response_model=AccountProfile)
async def get_profile(principal: CurrentPrincipalDependency) -> AccountProfile:
    return _profile(principal.user)


@router.patch("/api/account/profile", response_model=AccountProfile)
async def update_profile(
    payload: ProfileUpdateRequest,
    database: DatabaseDependency,
    principal: CsrfPrincipalDependency,
) -> AccountProfile:
    async with database.session() as session:
        user = await AccountRepository(session).get_user(principal.user.id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        user.display_name = payload.display_name
        await session.commit()
        return _profile(user)


@router.get("/api/account/agent-controls", response_model=AgentControlsResponse)
async def get_agent_controls(
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> AgentControlsResponse:
    async with database.session() as session:
        controls = await AccountRepository(session).get_controls(principal.user.id)
        if controls is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Shopping-agent controls are not initialized for this account",
            )
        return _controls_response(controls)


@router.put("/api/account/agent-controls", response_model=AgentControlsResponse)
async def update_agent_controls(
    payload: AgentControlsUpdate,
    database: DatabaseDependency,
    principal: CsrfPrincipalDependency,
) -> AgentControlsResponse:
    async with database.session() as session:
        controls = await AccountRepository(session).get_or_create_controls(principal.user.id)
        controls.agent_enabled = payload.agent_enabled
        controls.recommendation_price_ceiling_paise = (
            payload.recommendation_price_ceiling_paise
        )
        controls.per_purchase_limit_paise = payload.per_purchase_limit_paise
        controls.daily_spend_limit_paise = payload.daily_spend_limit_paise
        controls.monthly_spend_limit_paise = payload.monthly_spend_limit_paise
        controls.category_allowlist = [category.value for category in payload.category_allowlist]
        controls.max_recommendations = payload.max_recommendations
        await session.commit()
        return _controls_response(controls)


@router.get("/api/account/orders", response_model=OrderHistoryResponse)
async def get_order_history(
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> OrderHistoryResponse:
    async with database.session() as session:
        orders = await CommerceRepository(session).list_orders_for_buyer(principal.user.id)
    items: list[OrderHistoryItem] = []
    for order in orders:
        provider_order_id = order.provider_order_id
        provider_status = order.provider_status
        if (
            provider_order_id is None
            or provider_status is None
            or order.operation_state != ProviderOrderOperationState.CREATED.value
        ):
            continue
        items.append(
            OrderHistoryItem(
                order_id=order.id,
                run_id=order.purchase_run_id,
                quote_id=order.quote_id,
                provider_order_id=provider_order_id,
                status=provider_status,
                operation_state=order.operation_state,
                amount_paise=order.amount_paise,
                currency="INR",
                attempts=order.attempts,
                created_at=order.created_at,
                updated_at=order.updated_at,
            )
        )
    return OrderHistoryResponse(
        items=items,
        reason=None if items else "No provider-backed Razorpay Orders exist for this account.",
    )


@router.get("/api/account/transactions", response_model=TransactionHistoryResponse)
async def get_transaction_history(
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> TransactionHistoryResponse:
    async with database.session() as session:
        payments = await CommerceRepository(session).list_payments_for_buyer(principal.user.id)
    items = [
        TransactionHistoryItem(
            transaction_id=payment.id,
            run_id=payment.purchase_run_id,
            order_id=payment.razorpay_order_id,
            provider_payment_id=payment.provider_payment_id,
            provider_order_id=payment.provider_order_id,
            status=payment.status,
            captured=payment.captured,
            payment_method=payment.payment_method,
            error_code=payment.error_code,
            error_description=payment.error_description,
            amount_paise=payment.amount_paise,
            currency="INR",
            provider_created_at=payment.provider_created_at,
            captured_at=payment.captured_at,
            created_at=payment.created_at,
            updated_at=payment.updated_at,
        )
        for payment in payments
    ]
    return TransactionHistoryResponse(
        items=items,
        reason=None if items else "No provider-verified Razorpay payments exist for this account.",
    )


@router.get("/api/account/runs/{run_id}/audit", response_model=AuditHistoryResponse)
async def get_purchase_audit(
    run_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: CurrentPrincipalDependency,
) -> AuditHistoryResponse:
    async with database.session() as session:
        repository = CommerceRepository(session)
        run = await repository.get_run(run_id, buyer_user_id=principal.user.id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase run not found",
            )
        entries = await repository.list_audit_entries(
            run_id=run_id,
            buyer_user_id=principal.user.id,
        )
    signatures = _verified_audit_signatures(entries, settings)
    return AuditHistoryResponse(
        run_id=run_id,
        items=[
            AuditHistoryItem(
                audit_id=entry.id,
                run_id=entry.purchase_run_id,
                sequence_number=entry.sequence_number,
                actor=entry.actor,
                action=entry.action,
                outcome=entry.outcome,
                explanation=entry.explanation,
                details=dict(entry.details),
                previous_hash=entry.previous_hash,
                entry_hash=entry.entry_hash,
                signed=signature_verified,
                created_at=entry.created_at,
            )
            for entry, signature_verified in zip(entries, signatures, strict=True)
        ],
    )


# Every governed run, including proposal-only and pre-provider terminal outcomes.
from sqlalchemy import select as _history_select

from app.models.agent_order import AgentFulfillmentOrder as _AgentFulfillmentOrder
from app.models.commerce import PurchaseQuote as _PurchaseQuote
from app.models.commerce import RazorpayOrder as _RazorpayOrder
from app.models.purchase_run import PurchaseRun as _PurchaseRun
from app.schemas.agent_history import AgentRunHistoryItem as _AgentRunHistoryItem
from app.schemas.agent_history import (
    AgentRunHistoryResponse as _AgentRunHistoryResponse,
)


@router.get("/api/account/runs", response_model=_AgentRunHistoryResponse)
async def get_agent_run_history(
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> _AgentRunHistoryResponse:
    async with database.session() as session:
        result = await session.execute(
            _history_select(
                _PurchaseRun,
                _PurchaseQuote,
                _AgentFulfillmentOrder,
                _RazorpayOrder,
            )
            .outerjoin(
                _PurchaseQuote,
                _PurchaseQuote.purchase_run_id == _PurchaseRun.id,
            )
            .outerjoin(
                _AgentFulfillmentOrder,
                _AgentFulfillmentOrder.purchase_run_id == _PurchaseRun.id,
            )
            .outerjoin(
                _RazorpayOrder,
                _RazorpayOrder.purchase_run_id == _PurchaseRun.id,
            )
            .where(_PurchaseRun.buyer_user_id == principal.user.id)
            .order_by(_PurchaseRun.created_at.desc(), _PurchaseRun.id.desc())
            .limit(100)
        )
        rows = result.all()
    return _AgentRunHistoryResponse(
        items=[
            _AgentRunHistoryItem(
                run_id=run.id,
                conversation_id=run.conversation_id,
                conversation_turn_id=run.conversation_turn_id,
                state=run.state.value,
                payment_state=run.payment_state,
                terminal_reason=run.terminal_reason,
                provider_write_started=run.provider_write_started,
                quote_id=quote.id if quote is not None else None,
                product_id=quote.product_id if quote is not None else None,
                product_title=quote.title if quote is not None else None,
                amount_paise=quote.total_amount_paise if quote is not None else None,
                currency="INR",
                quote_expires_at=quote.expires_at if quote is not None else None,
                fulfillment_order_id=fulfillment.id if fulfillment is not None else None,
                fulfillment_order_number=(
                    fulfillment.order_number if fulfillment is not None else None
                ),
                fulfillment_status=fulfillment.status if fulfillment is not None else None,
                shipping_address=(
                    dict(fulfillment.shipping_address) if fulfillment is not None else None
                ),
                policy_snapshot=(
                    dict(fulfillment.policy_snapshot) if fulfillment is not None else {}
                ),
                provider_order_id=(
                    provider_order.provider_order_id if provider_order is not None else None
                ),
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            for run, quote, fulfillment, provider_order in rows
        ]
    )
