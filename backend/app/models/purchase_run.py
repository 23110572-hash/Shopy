"""Persisted idempotent agent/purchase run."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.purchase_state import PurchaseState
from backend.app.models.base import Base, TimestampMixin


class PurchaseRun(TimestampMixin, Base):
    __tablename__ = "purchase_runs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_replans BETWEEN 0 AND 10", name="max_replans_bounded"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_purchase_runs_buyer_created", "buyer_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    buyer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    merchant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[PurchaseState] = mapped_column(
        Enum(
            PurchaseState,
            name="purchase_state",
            values_callable=lambda enum: [item.value for item in enum],
            validate_strings=True,
        ),
        nullable=False,
        default=PurchaseState.RECEIVED,
        server_default=PurchaseState.RECEIVED.value,
    )
    graph_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_replans: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    provider_write_started: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    payment_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
