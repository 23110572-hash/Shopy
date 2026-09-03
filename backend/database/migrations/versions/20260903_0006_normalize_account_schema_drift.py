"""Normalize account constraints and preserve embedded catalogue images.

Revision ID: 20260903_0006
Revises: 20260903_0005
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20260903_0006"
down_revision: str | None = "20260903_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_RENAMES = (
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_a_9e7f",
        "ck_shopping_agent_controls_approval_threshold_positive",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_c_879f",
        "ck_shopping_agent_controls_currency_inr",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_d_275b",
        "ck_shopping_agent_controls_daily_spend_limit_positive",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_m_1a44",
        "ck_shopping_agent_controls_monthly_spend_limit_positive",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_m_5662",
        "ck_shopping_agent_controls_max_recommendations_bounded",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_m_9d44",
        "ck_shopping_agent_controls_max_replans_bounded",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_p_e8d1",
        "ck_shopping_agent_controls_per_purchase_limit_positive",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_r_7c9f",
        "ck_shopping_agent_controls_recommendation_ceiling_positive",
    ),
    (
        "ck_shopping_agent_controls_ck_shopping_agent_controls_v_50be",
        "ck_shopping_agent_controls_version_positive",
    ),
)


def _constraint_names(connection: Connection) -> set[str]:
    result = connection.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'shopping_agent_controls'::regclass"
        )
    )
    return set(result.scalars())


def _assert_rename_state(
    connection: Connection,
    renames: tuple[tuple[str, str], ...],
) -> None:
    names = _constraint_names(connection)
    missing = sorted(source for source, _ in renames if source not in names)
    conflicts = sorted(target for _, target in renames if target in names)
    if missing or conflicts:
        raise RuntimeError(
            "Unexpected shopping_agent_controls constraint state; "
            f"missing={missing!r}, conflicts={conflicts!r}"
        )


def _rename_constraints(renames: tuple[tuple[str, str], ...]) -> None:
    for source, target in renames:
        op.execute(
            sa.text(
                f'ALTER TABLE "shopping_agent_controls" '
                f'RENAME CONSTRAINT "{source}" TO "{target}"'
            )
        )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_rename_state(connection, CONSTRAINT_RENAMES)

    _rename_constraints(CONSTRAINT_RENAMES)
    op.alter_column(
        "products",
        "image_url",
        existing_type=sa.String(length=2048),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using='"image_url"::text',
    )


def downgrade() -> None:
    reverse_renames = tuple((target, source) for source, target in CONSTRAINT_RENAMES)
    connection = op.get_bind()
    _assert_rename_state(connection, reverse_renames)

    image_url_too_long = connection.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM products "
            "WHERE image_url IS NOT NULL AND char_length(image_url) > 2048"
            ")"
        )
    ).scalar_one()
    if image_url_too_long:
        raise RuntimeError(
            "Cannot downgrade products.image_url to 2048 characters: overlength data exists"
        )

    op.alter_column(
        "products",
        "image_url",
        existing_type=sa.Text(),
        type_=sa.String(length=2048),
        existing_nullable=True,
        postgresql_using='"image_url"::varchar(2048)',
    )
    _rename_constraints(reverse_renames)
