"""Persistent, buyer-owned Shopy Agent conversations and turns."""

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class AgentConversation(TimestampMixin, Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="status_valid"),
        CheckConstraint("turn_count >= 0", name="turn_count_non_negative"),
        CheckConstraint("replan_count BETWEEN 0 AND 3", name="replan_count_bounded"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_agent_conversations_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(160), nullable=False, default="New conversation", server_default="New conversation"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AgentConversationStatus.ACTIVE.value,
        server_default=AgentConversationStatus.ACTIVE.value,
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(240), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    replan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class AgentConversationTurn(TimestampMixin, Base):
    __tablename__ = "agent_conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_agent_turns_conversation_sequence"
        ),
        UniqueConstraint(
            "conversation_id", "client_turn_id", name="uq_agent_turns_conversation_client"
        ),
        CheckConstraint("sequence_number > 0", name="sequence_positive"),
        Index("ix_agent_turns_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_turn_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_reply: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    focus_product_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
