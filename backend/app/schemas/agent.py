"""Typed contracts for the Shopy catalogue and purchase-proposal agent."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.product import ProductCategory
from app.schemas.catalog import CatalogProduct

AgentIntentSource = Literal["deterministic", "openrouter", "deterministic_fallback"]
AgentDecisionSource = Literal["deterministic", "openrouter", "deterministic_fallback"]
ProposalBlocker = Literal["AUTH_REQUIRED", "PAYMENT_NOT_CONFIGURED", "STALE"]


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1_000)
    category: ProductCategory | None = None
    max_price_paise: int | None = Field(default=None, gt=0, le=1_000_000_000)
    limit: int = Field(default=4, ge=1, le=8)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Message must contain text")
        return normalized


class ShoppingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(max_length=160)
    category: ProductCategory | None
    max_price_paise: int | None = Field(gt=0, le=1_000_000_000)
    preferences: list[str] = Field(max_length=12)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("preferences")
    @classmethod
    def normalize_preferences(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            preference = " ".join(value.split()).strip().lower()
            if preference and preference not in normalized:
                normalized.append(preference[:40])
        return normalized


class AgentRuntimeControls(BaseModel):
    agent_enabled: bool
    recommendation_price_ceiling_paise: int | None
    per_purchase_limit_paise: int | None
    daily_spend_limit_paise: int | None
    monthly_spend_limit_paise: int | None
    category_allowlist: list[ProductCategory]
    max_recommendations: int = Field(ge=1, le=8)
    version: int = Field(ge=1)


class AgentRecommendation(BaseModel):
    product: CatalogProduct
    score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(min_length=1, max_length=6)


class ProductComparisonDecision(BaseModel):
    """Strict provider output; nullable fields remain required for OpenRouter schemas."""

    model_config = ConfigDict(extra="forbid")

    selected_product_id: UUID
    ranked_product_ids: list[UUID] = Field(min_length=1, max_length=8)
    winner_reason: str = Field(min_length=1, max_length=800)
    tradeoffs: list[str] = Field(max_length=5)
    upsell_product_id: UUID | None
    upsell_reason: str | None = Field(max_length=500)
    cross_sell_product_id: UUID | None
    cross_sell_reason: str | None = Field(max_length=500)

    @field_validator(
        "winner_reason",
        "upsell_reason",
        "cross_sell_reason",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = " ".join(value.split())
            return normalized or None
        return value

    @field_validator("tradeoffs")
    @classmethod
    def normalize_tradeoffs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = " ".join(value.split())[:240]
            if item and item not in normalized:
                normalized.append(item)
        return normalized


class AgentProductDecision(ProductComparisonDecision):
    decision_source: AgentDecisionSource


class ProposalHardLimits(BaseModel):
    requested_or_effective_ceiling_paise: int | None
    recommendation_ceiling_paise: int | None
    per_purchase_limit_paise: int | None
    daily_spend_limit_paise: int | None
    monthly_spend_limit_paise: int | None


class PurchaseProposal(BaseModel):
    proposal_id: UUID
    run_id: UUID
    product: CatalogProduct
    quantity: Literal[1] = 1
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    selection_source: AgentDecisionSource
    selection_reason: str
    product_version: int = Field(ge=1)
    controls_version: int = Field(ge=1)
    expires_at: datetime
    checkout_available: bool
    blocker: ProposalBlocker | None
    hard_limits: ProposalHardLimits


class AgentChatResponse(BaseModel):
    agent_name: Literal["Shopy Agent"] = "Shopy Agent"
    reply: str
    intent_source: AgentIntentSource
    decision_source: AgentDecisionSource | None = None
    intent: ShoppingIntent
    recommendations: list[AgentRecommendation]
    winner: AgentRecommendation | None = None
    decision: AgentProductDecision | None = None
    upsell: AgentRecommendation | None = None
    cross_sell: AgentRecommendation | None = None
    purchase_proposal: PurchaseProposal | None = None
    account_controls_applied: bool = False
    catalogue_backed: Literal[True] = True
    checkout_available: bool = False
    # Short, actionable note only. Empty when there is nothing useful to say.
    notice: str = ""
