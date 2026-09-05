"""Rename the seeded merchant brand to Shopy Limited.

Revision ID: 20260905_0013
Revises: 20260905_0012
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0013"
down_revision: str | None = "20260905_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE merchants SET name = :name, updated_at = now() "
            "WHERE slug = :slug"
        ).bindparams(name="Shopy Limited", slug="mandateguard-tech")
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE merchants SET name = :name, updated_at = now() "
            "WHERE slug = :slug"
        ).bindparams(name="MandateGuard Technology Store", slug="mandateguard-tech")
    )
