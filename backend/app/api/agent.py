"""Stateful LLM-first Shopy Agent and persisted governed purchase proposals."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select

from app.agents.shopping_graph import ShoppingGraph
from app.config import Settings
from app.database import Database
from app.dependencies import get_database, get_runtime_settings
from app.gateways.llm import LLMGateway
from app.gateways.openrouter import OpenRouterGateway
from app.models.account import ShoppingAgentControls
from app.models.conversation import (
    AgentConversation,
    AgentConversationStatus,
    AgentConversationTurn,
)
from app.repositories.accounts import AccountRepository
from app.repositories.conversations import ConversationRepository
from app.repositories.products import ProductRepository
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentConversationCreateRequest,
    AgentConversationDetail,
    AgentConversationList,
    AgentConversationSummary,
    AgentConversationTurnResponse,
    AgentRuntimeControls,
)
from app.schemas.catalog import CatalogProduct
from app.security import (
    CsrfPrincipalDependency,
    CurrentPrincipalDependency,
    OptionalPrincipalDependency,
)
from app.services.proposals import ProposalStaleError, persist_purchase_proposal

router = APIRouter(prefix="/api/agent", tags=["agent"])
DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def _controls(saved: ShoppingAgentControls) -> AgentRuntimeControls:
    return AgentRuntimeControls(
        agent_enabled=saved.agent_enabled,
        recommendation_price_ceiling_paise=saved.recommendation_price_ceiling_paise,
        per_purchase_limit_paise=saved.per_purchase_limit_paise,
        daily_spend_limit_paise=saved.daily_spend_limit_paise,
        monthly_spend_limit_paise=saved.monthly_spend_limit_paise,
        category_allowlist=list(saved.category_allowlist),
        max_recommendations=saved.max_recommendations,
        version=saved.version,
    )


def _summary(conversation: AgentConversation) -> AgentConversationSummary:
    return AgentConversationSummary(
        conversation_id=conversation.id,
        title=conversation.title,
        status=conversation.status,
        last_message_preview=conversation.last_message_preview,
        turn_count=conversation.turn_count,
        replan_count=conversation.replan_count,
        version=conversation.version,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _turn_response(turn: AgentConversationTurn) -> AgentConversationTurnResponse:
    return AgentConversationTurnResponse(
        turn_id=turn.id,
        sequence_number=turn.sequence_number,
        client_turn_id=turn.client_turn_id,
        user_message=turn.user_message,
        assistant_reply=turn.assistant_reply,
        outcome=turn.outcome,
        response=AgentChatResponse.model_validate(turn.response_payload),
        created_at=turn.created_at,
    )


def _not_found() -> NoReturn:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


def _safe_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _context_product_ids(context: dict[str, object]) -> list[UUID]:
    identifiers: list[UUID] = []
    for key in ("last_recommendation_ids", "pending_option_ids"):
        values = context.get(key)
        if not isinstance(values, list):
            continue
        for value in values[:8]:
            identifier = _safe_uuid(value)
            if identifier is not None and identifier not in identifiers:
                identifiers.append(identifier)
    focus = _safe_uuid(context.get("focus_product_id"))
    if focus is not None and focus not in identifiers:
        identifiers.insert(0, focus)
    return identifiers[:12]


def _context_list(context: dict[str, object], key: str) -> list[object]:
    value = context.get(key)
    return list(value) if isinstance(value, list) else []


def _next_context(
    *,
    previous: dict[str, object],
    response: AgentChatResponse,
    request: AgentChatRequest,
) -> dict[str, object]:
    recommendation_ids = [str(item.product.id) for item in response.recommendations[:8]]
    has_new_product_state = bool(recommendation_ids or response.focus_product_id)
    prior_intent = previous.get("intent")
    prior_mode = previous.get("intent_mode")
    persistent_intent_mode = (
        prior_mode
        if response.intent_mode == "OTHER"
        and prior_mode in {"BUY", "RECOMMEND", "COMPARE"}
        else response.intent_mode
    )
    context: dict[str, object] = {
        "schema_version": 3,
        "intent": (
            response.intent.model_dump(mode="json")
            if has_new_product_state or not isinstance(prior_intent, dict)
            else prior_intent
        ),
        "intent_mode": persistent_intent_mode,
        "last_recommendation_ids": (
            recommendation_ids
            if recommendation_ids
            else _context_list(previous, "last_recommendation_ids")
        ),
        "focus_product_id": (
            str(response.focus_product_id)
            if response.focus_product_id is not None
            else previous.get("focus_product_id")
        ),
        "pending_option_ids": (
            [str(option.product_id) for option in response.clarification.options]
            if response.clarification is not None
            else []
        ),
    }
    if previous.get("cross_sell_declined") is True:
        context["cross_sell_declined"] = True
    if request.cross_sell_consent is False:
        context["cross_sell_declined"] = True
    elif request.cross_sell_consent is True:
        context["cross_sell_declined"] = False
    return context


@router.post("/conversations", response_model=AgentConversationSummary, status_code=201)
async def create_conversation(
    payload: AgentConversationCreateRequest,
    database: DatabaseDependency,
    principal: CsrfPrincipalDependency,
) -> AgentConversationSummary:
    async with database.session() as session:
        conversation = await ConversationRepository(session).create(
            user_id=principal.user.id,
            title=payload.title,
        )
        await session.commit()
        await session.refresh(conversation)
        return _summary(conversation)


@router.get("/conversations", response_model=AgentConversationList)
async def list_conversations(
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> AgentConversationList:
    async with database.session() as session:
        items = await ConversationRepository(session).list_for_user(principal.user.id)
        return AgentConversationList(items=[_summary(item) for item in items])


@router.get("/conversations/{conversation_id}", response_model=AgentConversationDetail)
async def get_conversation(
    conversation_id: UUID,
    database: DatabaseDependency,
    principal: CurrentPrincipalDependency,
) -> AgentConversationDetail:
    async with database.session() as session:
        repository = ConversationRepository(session)
        conversation = await repository.get_owned(
            conversation_id, user_id=principal.user.id
        )
        if conversation is None:
            _not_found()
        turns = await repository.list_turns(conversation.id)
        summary = _summary(conversation)
        return AgentConversationDetail(
            **summary.model_dump(),
            turns=[_turn_response(turn) for turn in turns],
        )


@router.delete("/conversations", status_code=204, response_class=Response)
async def clear_conversations(
    database: DatabaseDependency,
    principal: CsrfPrincipalDependency,
) -> Response:
    async with database.session() as session:
        conversation_ids = select(AgentConversation.id).where(
            AgentConversation.user_id == principal.user.id
        )
        await session.execute(
            delete(AgentConversationTurn).where(
                AgentConversationTurn.conversation_id.in_(conversation_ids)
            )
        )
        await session.execute(
            delete(AgentConversation).where(
                AgentConversation.user_id == principal.user.id
            )
        )
        await session.commit()
    return Response(status_code=204)


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response)
async def close_conversation(
    conversation_id: UUID,
    database: DatabaseDependency,
    principal: CsrfPrincipalDependency,
) -> Response:
    async with database.session() as session:
        repository = ConversationRepository(session)
        conversation = await repository.get_owned(
            conversation_id, user_id=principal.user.id, for_update=True
        )
        if conversation is None:
            _not_found()
        await repository.close(conversation)
        await session.commit()
    return Response(status_code=204)


@router.post("/chat", response_model=AgentChatResponse)
async def chat_with_agent(
    request: AgentChatRequest,
    database: DatabaseDependency,
    settings: SettingsDependency,
    principal: OptionalPrincipalDependency,
) -> AgentChatResponse:
    llm_gateway: LLMGateway | None = (
        OpenRouterGateway(settings) if settings.openrouter_configured else None
    )
    async with database.session() as session:
        products = ProductRepository(session)
        saved_controls: ShoppingAgentControls | None = None
        runtime_controls: AgentRuntimeControls | None = None
        conversation: AgentConversation | None = None
        conversation_repository = ConversationRepository(session)

        if principal is not None:
            saved_controls = await AccountRepository(session).get_or_create_controls(
                principal.user.id
            )
            runtime_controls = _controls(saved_controls)
            if request.conversation_id is None:
                conversation = await conversation_repository.create(
                    user_id=principal.user.id,
                    title=request.message[:160],
                )
            else:
                conversation = await conversation_repository.get_owned(
                    request.conversation_id,
                    user_id=principal.user.id,
                )
                if conversation is None:
                    _not_found()
            if conversation.status != AgentConversationStatus.ACTIVE.value:
                raise HTTPException(status_code=409, detail="Conversation is closed")
            if (
                request.expected_conversation_version is not None
                and request.expected_conversation_version != conversation.version
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "CONVERSATION_CHANGED",
                        "message": "This conversation changed. Reload it before sending again.",
                    },
                )
            duplicate = await conversation_repository.get_turn_by_client_id(
                conversation_id=conversation.id,
                client_turn_id=request.client_turn_id,
            )
            if duplicate is not None:
                return AgentChatResponse.model_validate(duplicate.response_payload)

        conversation_context = dict(conversation.context) if conversation is not None else {}
        recent_turns = (
            await conversation_repository.list_recent_turns(conversation.id, limit=6)
            if conversation is not None
            else []
        )
        llm_conversation_context: dict[str, object] = {
            **conversation_context,
            "profile_display_name": (
                principal.user.display_name if principal is not None else None
            ),
            "recent_turns": [
                {
                    "user": turn.user_message[:500],
                    "assistant": turn.assistant_reply[:500],
                }
                for turn in recent_turns
            ],
        }
        reference_products = [
            CatalogProduct.model_validate(product)
            for product in await products.get_active_many(
                _context_product_ids(conversation_context)
            )
        ]
        agent_response = await ShoppingGraph(
            products,
            llm_gateway,
            controls=runtime_controls,
            conversation_context=llm_conversation_context,
            reference_products=reference_products,
            cross_sell_allowed=request.cross_sell_consent is True,
        ).chat(request)

        if conversation is None:
            if principal is None and agent_response.winner is not None:
                return agent_response.model_copy(
                    update={
                        "notice": (
                            "Sign in to choose or add a saved address and continue to Razorpay."
                            if agent_response.intent_mode == "BUY"
                            else "Sign in when you want the Agent to buy a selected product."
                        )
                    }
                )
            return agent_response

        turn = AgentConversationTurn(
            conversation_id=conversation.id,
            client_turn_id=request.client_turn_id,
            sequence_number=conversation.turn_count + 1,
            user_message=request.message,
            assistant_reply=agent_response.reply,
            outcome=agent_response.outcome,
            response_payload={},
            focus_product_id=agent_response.focus_product_id,
        )
        session.add(turn)
        await session.flush()

        if (
            principal is not None
            and saved_controls is not None
            and agent_response.winner is not None
            and agent_response.outcome == "RECOMMENDATIONS"
            and agent_response.intent_mode == "BUY"
            and request.cross_sell_consent is None
        ):
            try:
                proposal = await persist_purchase_proposal(
                    session=session,
                    settings=settings,
                    buyer_user_id=principal.user.id,
                    request=request,
                    response=agent_response,
                    controls=saved_controls,
                    conversation_id=conversation.id,
                    conversation_turn_id=turn.id,
                )
            except ProposalStaleError:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PRODUCT_CHANGED",
                        "message": "The product changed before the quote was saved. Compare again.",
                    },
                ) from None
            if proposal is not None:
                agent_response = agent_response.model_copy(
                    update={
                        "purchase_proposal": proposal,
                        "checkout_available": proposal.checkout_available,
                        "notice": (
                            "Choose or add a saved delivery address, confirm it for this order, "
                            "then continue in Razorpay Test Mode."
                            if proposal.checkout_available
                            else "Razorpay Test Mode is not configured yet."
                        ),
                    }
                )

        if request.cross_sell_consent is False:
            agent_response = agent_response.model_copy(
                update={
                    "notice": "Optional add-ons were declined and will not be bundled or searched.",
                    "cross_sell_consent_required": False,
                }
            )

        conversation.context = _next_context(
            previous=conversation_context,
            response=agent_response,
            request=request,
        )
        conversation.turn_count += 1
        conversation.last_message_preview = request.message[:240]
        await session.flush()

        agent_response = agent_response.model_copy(
            update={
                "conversation_id": conversation.id,
                "conversation_version": conversation.version,
                "turn_id": turn.id,
                "replan_count": conversation.replan_count,
                "remaining_replans": 3 - conversation.replan_count,
            }
        )
        turn.assistant_reply = agent_response.reply
        turn.outcome = agent_response.outcome
        turn.response_payload = agent_response.model_dump(mode="json")
        await session.commit()
        return agent_response
