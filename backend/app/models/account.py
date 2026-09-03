"""Database-backed authentication sessions and shopping-agent controls."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShoppingAgentControls(TimestampMixin, Base):
    __tablename__ = "shopping_agent_controls"
    __table_args__ = (
        CheckConstraint(
            "recommendation_price_ceiling_paise IS NULL "
            "OR recommendation_price_ceiling_paise > 0",
            name="recommendation_ceiling_positive",
        ),
        CheckConstraint(
            "per_purchase_limit_paise IS NULL OR per_purchase_limit_paise > 0",
            name="per_purchase_limit_positive",
        ),
        CheckConstraint(
            "daily_spend_limit_paise IS NULL OR daily_spend_limit_paise > 0",
            name="daily_spend_limit_positive",
        ),
        CheckConstraint(
            "monthly_spend_limit_paise IS NULL OR monthly_spend_limit_paise > 0",
            name="monthly_spend_limit_positive",
        ),
        CheckConstraint(
            "approval_required_above_paise IS NULL "
            "OR approval_required_above_paise > 0",
            name="approval_threshold_positive",
        ),
        CheckConstraint(
            "max_recommendations BETWEEN 1 AND 8",
            name="max_recommendations_bounded",
        ),
        CheckConstraint("max_replans BETWEEN 0 AND 10", name="max_replans_bounded"),
        CheckConstraint("currency = 'INR'", name="currency_inr"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    recommendation_price_ceiling_paise: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    per_purchase_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_spend_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_spend_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_required_above_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category_allowlist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    max_recommendations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default=text("4")
    )
    max_replans: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    allow_substitutions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="INR", server_default="INR"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
