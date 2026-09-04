"""Add persistent Shopy Agent conversations.

Revision ID: 20260904_0009
Revises: 20260904_0008
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0009"
down_revision: str | None = "20260904_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), server_default="New conversation", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("last_message_preview", sa.String(length=240), nullable=True),
        sa.Column("turn_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("replan_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name=op.f("ck_agent_conversations_status_valid")),
        sa.CheckConstraint("turn_count >= 0", name=op.f("ck_agent_conversations_turn_count_non_negative")),
        sa.CheckConstraint("replan_count BETWEEN 0 AND 3", name=op.f("ck_agent_conversations_replan_count_bounded")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_conversations_version_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_agent_conversations_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversations")),
    )
    op.create_index("ix_agent_conversations_user_updated", "agent_conversations", ["user_id", "updated_at"])

    op.create_table(
        "agent_conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("client_turn_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_reply", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("focus_product_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("sequence_number > 0", name=op.f("ck_agent_conversation_turns_sequence_positive")),
        sa.ForeignKeyConstraint(["conversation_id"], ["agent_conversations.id"], name=op.f("fk_agent_conversation_turns_conversation_id_agent_conversations"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["focus_product_id"], ["products.id"], name=op.f("fk_agent_conversation_turns_focus_product_id_products"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversation_turns")),
        sa.UniqueConstraint("conversation_id", "sequence_number", name=op.f("uq_agent_turns_conversation_sequence")),
        sa.UniqueConstraint("conversation_id", "client_turn_id", name=op.f("uq_agent_turns_conversation_client")),
    )
    op.create_index("ix_agent_turns_conversation_created", "agent_conversation_turns", ["conversation_id", "created_at"])

    op.add_column("purchase_runs", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.add_column("purchase_runs", sa.Column("conversation_turn_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f("fk_purchase_runs_conversation_id_agent_conversations"), "purchase_runs", "agent_conversations", ["conversation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_purchase_runs_conversation_turn_id_agent_conversation_turns"), "purchase_runs", "agent_conversation_turns", ["conversation_turn_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_purchase_runs_conversation_created", "purchase_runs", ["conversation_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_purchase_runs_conversation_created", table_name="purchase_runs")
    op.drop_constraint(op.f("fk_purchase_runs_conversation_turn_id_agent_conversation_turns"), "purchase_runs", type_="foreignkey")
    op.drop_constraint(op.f("fk_purchase_runs_conversation_id_agent_conversations"), "purchase_runs", type_="foreignkey")
    op.drop_column("purchase_runs", "conversation_turn_id")
    op.drop_column("purchase_runs", "conversation_id")
    op.drop_index("ix_agent_turns_conversation_created", table_name="agent_conversation_turns")
    op.drop_table("agent_conversation_turns")
    op.drop_index("ix_agent_conversations_user_updated", table_name="agent_conversations")
    op.drop_table("agent_conversations")
