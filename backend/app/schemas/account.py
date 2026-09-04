"""Typed account, authentication, history, audit, and agent-control contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.models.product import ProductCategory
from app.models.user import UserRole

MAX_MONEY_PAISE = 1_000_000_000


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or any(character.isspace() for character in normalized):
        raise ValueError("Enter a valid email address")
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain or "." not in domain:
        raise ValueError("Enter a valid email address")
    if local.startswith(".") or local.endswith(".") or domain.startswith("."):
        raise ValueError("Enter a valid email address")
    return normalized


def _normalize_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) < 2:
        raise ValueError("Display name must contain at least 2 characters")
    return normalized


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    display_name: str = Field(min_length=2, max_length=120)
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return _normalize_display_name(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if not 10 <= len(password) <= 128:
            raise ValueError("Password must be between 10 and 128 characters")
        if not any(character.isalpha() for character in password) or not any(
            character.isdigit() for character in password
        ):
            raise ValueError("Password must contain a letter and a number")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("password")
    @classmethod
    def bound_password(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password is invalid")
        return value


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=2, max_length=120)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return _normalize_display_name(value)


class AccountProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    role: UserRole
    email_verified: bool
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class AuthResponse(BaseModel):
    profile: AccountProfile
    message: str


class AuthMessage(BaseModel):
    message: str


class AgentControlsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_enabled: bool = True
    recommendation_price_ceiling_paise: int | None = Field(
        default=None, gt=0, le=MAX_MONEY_PAISE
    )
    per_purchase_limit_paise: int | None = Field(default=None, gt=0, le=MAX_MONEY_PAISE)
    daily_spend_limit_paise: int | None = Field(default=None, gt=0, le=MAX_MONEY_PAISE)
    monthly_spend_limit_paise: int | None = Field(default=None, gt=0, le=MAX_MONEY_PAISE)
    category_allowlist: list[ProductCategory] = Field(default_factory=list, max_length=5)
    max_recommendations: int = Field(default=4, ge=1, le=8)

    @field_validator("category_allowlist")
    @classmethod
    def unique_categories(cls, values: list[ProductCategory]) -> list[ProductCategory]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_spending_limits(self) -> "AgentControlsUpdate":
        per_purchase = self.per_purchase_limit_paise
        daily = self.daily_spend_limit_paise
        monthly = self.monthly_spend_limit_paise
        if per_purchase is not None and daily is not None and daily < per_purchase:
            raise ValueError("Daily limit cannot be below the per-purchase limit")
        if daily is not None and monthly is not None and monthly < daily:
            raise ValueError("Monthly limit cannot be below the daily limit")
        return self


class AgentControlsResponse(AgentControlsUpdate):
    user_id: UUID
    currency: Literal["INR"] = "INR"
    version: int
    updated_at: datetime
    purchase_authority: Literal["explicit_checkout_only"] = "explicit_checkout_only"
    purchase_authority_notice: str = (
        "These limits cap what the agent can recommend and buy, but it can never pay by "
        "itself. You confirm every payment in Razorpay."
    )


class OrderHistoryItem(BaseModel):
    order_id: UUID
    run_id: UUID
    quote_id: UUID
    product_id: UUID
    product_title: str
    product_brand: str
    product_model: str
    product_sku: str
    product_category: str
    quantity: int = Field(gt=0)
    provider_order_id: str
    provider: Literal["razorpay"] = "razorpay"
    status: str
    operation_state: str
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    attempts: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class TransactionHistoryItem(BaseModel):
    transaction_id: UUID
    run_id: UUID
    order_id: UUID
    quote_id: UUID
    product_id: UUID
    product_title: str
    product_brand: str
    product_model: str
    product_sku: str
    product_category: str
    quantity: int = Field(gt=0)
    provider_payment_id: str
    provider_order_id: str
    provider: Literal["razorpay"] = "razorpay"
    status: str
    captured: bool
    payment_method: str | None
    error_code: str | None
    error_description: str | None
    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    provider_created_at: datetime | None
    captured_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OrderHistoryResponse(BaseModel):
    availability: Literal["available"] = "available"
    items: list[OrderHistoryItem] = Field(default_factory=list)
    reason: str | None = None


class TransactionHistoryResponse(BaseModel):
    availability: Literal["available"] = "available"
    items: list[TransactionHistoryItem] = Field(default_factory=list)
    reason: str | None = None


class AuditHistoryItem(BaseModel):
    audit_id: UUID
    run_id: UUID
    sequence_number: int = Field(gt=0)
    actor: str
    action: str
    outcome: str
    explanation: str
    details: dict[str, object]
    previous_hash: str
    entry_hash: str
    signed: bool
    created_at: datetime


class AuditHistoryResponse(BaseModel):
    run_id: UUID
    availability: Literal["available"] = "available"
    items: list[AuditHistoryItem] = Field(default_factory=list)
