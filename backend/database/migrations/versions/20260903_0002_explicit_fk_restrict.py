"""Make ownership foreign-key deletion behavior explicit.

Revision ID: 20260903_0002
Revises: 20260903_0001
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0002"
down_revision: str | None = "20260903_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_merchants_owner_user_id_users", "merchants", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_merchants_owner_user_id_users",
        "merchants",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "fk_purchase_runs_buyer_user_id_users", "purchase_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_purchase_runs_buyer_user_id_users",
        "purchase_runs",
        "users",
        ["buyer_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "fk_purchase_runs_merchant_id_merchants", "purchase_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_purchase_runs_merchant_id_merchants",
        "purchase_runs",
        "merchants",
        ["merchant_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_purchase_runs_merchant_id_merchants", "purchase_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_purchase_runs_merchant_id_merchants",
        "purchase_runs",
        "merchants",
        ["merchant_id"],
        ["id"],
    )

    op.drop_constraint(
        "fk_purchase_runs_buyer_user_id_users", "purchase_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_purchase_runs_buyer_user_id_users",
        "purchase_runs",
        "users",
        ["buyer_user_id"],
        ["id"],
    )

    op.drop_constraint(
        "fk_merchants_owner_user_id_users", "merchants", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_merchants_owner_user_id_users",
        "merchants",
        "users",
        ["owner_user_id"],
        ["id"],
    )
