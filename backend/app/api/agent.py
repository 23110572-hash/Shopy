"""Stateful, bounded Shopy Agent and persisted purchase proposals."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

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
from app.models.product import ProductCategory
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
    ShoppingIntent,
)
from app.security import (
    CsrfPrincipalDependency,
    CurrentPrincipalDependency,
    OptionalPrincipalDependency,
)
from app.services.agent_intelligence import (
    conversation_context_after_response,
    plan_agent_turn,
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
        category_allowlist=[ProductCategory(value) for value in saved.category_allowlist],
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

        if conversation is None:
            response = await ShoppingGraph(
                products,
                llm_gateway,
                controls=runtime_controls,
                cross_sell_allowed=request.cross_sell_consent is True,
            ).chat(request)
            if principal is None and response.winner is not None:
                return response.model_copy(
                    update={"notice": "Sign in to buy this with Razorpay Test Mode."}
                )
            return response

        identity_products = list(await products.list_identity_candidates())
        plan = plan_agent_turn(
            request=request,
            conversation_context=dict(conversation.context),
            products=identity_products,
            replan_count=conversation.replan_count,
        )

        if plan.clarification is not None:
            prior = plan.inherited_intent or ShoppingIntent(
                query="", category=request.category, max_price_paise=request.max_price_paise, preferences=[]
            )
            agent_response = AgentChatResponse(
                reply=plan.clarification.question,
                intent_source="deterministic",
                intent=prior,
                recommendations=[],
                account_controls_applied=runtime_controls is not None,
                outcome="CLARIFICATION",
                resolution_kind="CLARIFICATION_REQUIRED",
                clarification=plan.clarification,
                replan_count=conversation.replan_count,
                remaining_replans=3 - conversation.replan_count,
            )
        elif plan.resolution_hint == "NO_MATCH" and conversation.replan_count >= 3:
            prior = plan.inherited_intent or ShoppingIntent(
                query="", category=request.category, max_price_paise=request.max_price_paise, preferences=[]
            )
            agent_response = AgentChatResponse(
                reply="This conversation has reached its three safe replans. Start a new conversation with updated requirements.",
                intent_source="deterministic",
                intent=prior,
                recommendations=[],
                account_controls_applied=True,
                outcome="NO_MATCH",
                resolution_kind="NO_MATCH",
                replan_count=conversation.replan_count,
                remaining_replans=0,
            )
        else:
            agent_response = await ShoppingGraph(
                products,
                llm_gateway,
                controls=runtime_controls,
                forced_product_id=plan.forced_product_id,
                excluded_product_ids=plan.excluded_product_ids,
                previous_intent=plan.inherited_intent,
                exact_match=plan.exact_match,
                cross_sell_allowed=plan.cross_sell_allowed,
            ).chat(request)

        if plan.replan_increment:
            conversation.replan_count += 1

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
                        "notice": "" if proposal.checkout_available else "Online payment is not configured yet.",
                    }
                )

        recommendation_ids = [item.product.id for item in agent_response.recommendations]
        next_context = conversation_context_after_response(
            previous=dict(conversation.context),
            intent=agent_response.intent,
            recommendation_ids=recommendation_ids,
            focus_product_id=agent_response.focus_product_id,
            clarification=agent_response.clarification,
        )
        if request.cross_sell_consent is False:
            next_context["cross_sell_declined"] = True
            agent_response = agent_response.model_copy(
                update={
                    "notice": "Optional add-ons were declined and will not be bundled or searched.",
                    "cross_sell_consent_required": False,
                }
            )
        elif request.cross_sell_consent is True:
            next_context["cross_sell_declined"] = False
        conversation.context = next_context
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
