"""Add authenticated accounts and persisted shopping-agent controls.

Revision ID: 20260903_0005
Revises: 20260903_0004
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0005"
down_revision: str | None = "20260903_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT lower(trim(email))
                FROM users
                GROUP BY lower(trim(email))
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot normalize user emails: duplicates exist';
            END IF;
        END
        $$;
        """
    )
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_users_email_normalized",
        "users",
        [sa.text("lower(trim(email))")],
        unique=True,
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("token_digest", name="uq_auth_sessions_token_digest"),
    )
    op.create_index(
        "ix_auth_sessions_user_active",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "shopping_agent_controls",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("recommendation_price_ceiling_paise", sa.Integer(), nullable=True),
        sa.Column("per_purchase_limit_paise", sa.Integer(), nullable=True),
        sa.Column("daily_spend_limit_paise", sa.Integer(), nullable=True),
        sa.Column("monthly_spend_limit_paise", sa.Integer(), nullable=True),
        sa.Column("approval_required_above_paise", sa.Integer(), nullable=True),
        sa.Column(
            "category_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("max_recommendations", sa.Integer(), server_default="4", nullable=False),
        sa.Column("max_replans", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "allow_substitutions",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            "recommendation_price_ceiling_paise IS NULL "
            "OR recommendation_price_ceiling_paise > 0",
            name="ck_shopping_agent_controls_recommendation_ceiling_positive",
        ),
        sa.CheckConstraint(
            "per_purchase_limit_paise IS NULL OR per_purchase_limit_paise > 0",
            name="ck_shopping_agent_controls_per_purchase_limit_positive",
        ),
        sa.CheckConstraint(
            "daily_spend_limit_paise IS NULL OR daily_spend_limit_paise > 0",
            name="ck_shopping_agent_controls_daily_spend_limit_positive",
        ),
        sa.CheckConstraint(
            "monthly_spend_limit_paise IS NULL OR monthly_spend_limit_paise > 0",
            name="ck_shopping_agent_controls_monthly_spend_limit_positive",
        ),
        sa.CheckConstraint(
            "approval_required_above_paise IS NULL OR approval_required_above_paise > 0",
            name="ck_shopping_agent_controls_approval_threshold_positive",
        ),
        sa.CheckConstraint(
            "max_recommendations BETWEEN 1 AND 8",
            name="ck_shopping_agent_controls_max_recommendations_bounded",
        ),
        sa.CheckConstraint(
            "max_replans BETWEEN 0 AND 10",
            name="ck_shopping_agent_controls_max_replans_bounded",
        ),
        sa.CheckConstraint(
            "currency = 'INR'",
            name="ck_shopping_agent_controls_currency_inr",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_shopping_agent_controls_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_shopping_agent_controls_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_shopping_agent_controls"),
    )
    op.create_index(
        "ix_purchase_runs_buyer_created",
        "purchase_runs",
        ["buyer_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_runs_buyer_created", table_name="purchase_runs")
    op.drop_table("shopping_agent_controls")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email_normalized", table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "password_hash")
