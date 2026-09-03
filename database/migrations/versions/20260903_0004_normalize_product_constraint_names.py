"""Normalize product check-constraint names after naming-convention expansion.

Revision ID: 20260903_0004
Revises: 20260903_0003
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0004"
down_revision: str | None = "20260903_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_SUFFIXES = (
    "inventory_non_negative",
    "mrp_not_below_offer",
    "offer_price_positive",
    "source_url_https",
    "version_positive",
)


def upgrade() -> None:
    for suffix in CONSTRAINT_SUFFIXES:
        op.execute(
            f"ALTER TABLE products RENAME CONSTRAINT "
            f"ck_products_ck_products_{suffix} TO ck_products_{suffix}"
        )


def downgrade() -> None:
    for suffix in CONSTRAINT_SUFFIXES:
        op.execute(
            f"ALTER TABLE products RENAME CONSTRAINT "
            f"ck_products_{suffix} TO ck_products_ck_products_{suffix}"
        )
