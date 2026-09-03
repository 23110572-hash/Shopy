"""HTTP entrypoint for bounded Shopy comparison and persisted purchase proposals."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agents.shopping_graph import ShoppingGraph
from app.config import Settings
from app.database import Database
from app.dependencies import get_database, get_runtime_settings
from app.gateways.llm import LLMGateway
from app.gateways.openrouter import OpenRouterGateway
from app.models.account import ShoppingAgentControls
from app.models.product import ProductCategory
from app.repositories.accounts import AccountRepository
from app.repositories.products import ProductRepository
from app.schemas.agent import AgentChatRequest, AgentChatResponse, AgentRuntimeControls
from app.security import OptionalPrincipalDependency
from app.services.proposals import ProposalStaleError, persist_purchase_proposal

router = APIRouter(prefix="/api/agent", tags=["agent"])
DatabaseDependency = Annotated[Database, Depends(get_database)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


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
        saved_controls: ShoppingAgentControls | None = None
        runtime_controls: AgentRuntimeControls | None = None
        if principal is not None:
            saved_controls = await AccountRepository(session).get_or_create_controls(
                principal.user.id
            )
            runtime_controls = AgentRuntimeControls(
                agent_enabled=saved_controls.agent_enabled,
                recommendation_price_ceiling_paise=(
                    saved_controls.recommendation_price_ceiling_paise
                ),
                per_purchase_limit_paise=saved_controls.per_purchase_limit_paise,
                daily_spend_limit_paise=saved_controls.daily_spend_limit_paise,
                monthly_spend_limit_paise=saved_controls.monthly_spend_limit_paise,
                category_allowlist=[
                    ProductCategory(value) for value in saved_controls.category_allowlist
                ],
                max_recommendations=saved_controls.max_recommendations,
                version=saved_controls.version,
            )

        graph = ShoppingGraph(
            ProductRepository(session),
            llm_gateway,
            controls=runtime_controls,
        )
        agent_response = await graph.chat(request)

        if principal is None:
            if agent_response.winner is None:
                return agent_response
            return agent_response.model_copy(
                update={"notice": "Sign in to buy this with Razorpay Test Mode."}
            )

        if saved_controls is None or agent_response.winner is None:
            await session.commit()
            return agent_response

        try:
            proposal = await persist_purchase_proposal(
                session=session,
                settings=settings,
                buyer_user_id=principal.user.id,
                request=request,
                response=agent_response,
                controls=saved_controls,
            )
        except ProposalStaleError:
            await session.rollback()
            return agent_response.model_copy(
                update={
                    "notice": (
                        "This product just changed, so nothing was saved. Ask me to compare again."
                    )
                }
            )

        if proposal is None:
            await session.commit()
            return agent_response
        await session.commit()
        return agent_response.model_copy(
            update={
                "purchase_proposal": proposal,
                "checkout_available": proposal.checkout_available,
                "notice": (
                    ""
                    if proposal.checkout_available
                    else "Online payment is not configured yet."
                ),
            }
        )
