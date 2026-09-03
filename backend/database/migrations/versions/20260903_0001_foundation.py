"""Create authenticated principals, merchant, and persisted purchase runs.

Revision ID: 20260903_0001
Revises: None
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ROLES = ("buyer", "merchant_admin")
PURCHASE_STATES = (
    "RECEIVED",
    "INTENT_PARSED",
    "SEARCHING",
    "PRODUCT_SELECTED",
    "QUOTED",
    "QUOTE_VALIDATED",
    "CANDIDATE_REJECTED",
    "REPLANNING",
    "NO_ELIGIBLE_PRODUCT",
    "POLICY_APPROVED",
    "POLICY_DENIED",
    "RESERVED",
    "ORDER_CREATED",
    "PAYMENT_INITIATED",
    "CAPTURED",
    "PAYMENT_FAILED",
    "PAYMENT_UNKNOWN",
    "NEEDS_USER_AUTH",
)


def upgrade() -> None:
    bind = op.get_bind()
    user_role = postgresql.ENUM(*USER_ROLES, name="user_role", create_type=False)
    purchase_state = postgresql.ENUM(
        *PURCHASE_STATES, name="purchase_state", create_type=False
    )
    user_role.create(bind, checkfirst=True)
    purchase_state.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("role", user_role, server_default="buyer", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_merchants_owner_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchants")),
    )
    op.create_index("ix_merchants_owner_user_id", "merchants", ["owner_user_id"])
    op.create_index("ix_merchants_slug", "merchants", ["slug"], unique=True)

    op.create_table(
        "purchase_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("buyer_user_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column(
            "state", purchase_state, server_default="RECEIVED", nullable=False
        ),
        sa.Column(
            "graph_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_replans", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "provider_write_started",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("payment_state", sa.String(length=80), nullable=True),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_purchase_runs_attempt_count_non_negative")
        ),
        sa.CheckConstraint(
            "max_replans BETWEEN 0 AND 10",
            name=op.f("ck_purchase_runs_max_replans_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["buyer_user_id"], ["users.id"], name=op.f("fk_purchase_runs_buyer_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], name=op.f("fk_purchase_runs_merchant_id_merchants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_runs")),
    )
    op.create_index(
        "ix_purchase_runs_idempotency_key", "purchase_runs", ["idempotency_key"], unique=True
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("purchase_runs")
    op.drop_table("merchants")
    op.drop_table("users")
    postgresql.ENUM(name="purchase_state").drop(bind, checkfirst=True)
    postgresql.ENUM(name="user_role").drop(bind, checkfirst=True)
