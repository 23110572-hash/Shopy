"""Add catalogue-authored post-purchase cross-sell relationships.

Revision ID: 20260905_0012
Revises: 20260904_0011
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0012"
down_revision: str | None = "20260904_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RELATIONSHIPS = (
    (
        "smartphones",
        "headphones",
        "Wireless headphones add private listening and hands-free calls to this phone.",
    ),
    (
        "tablets",
        "headphones",
        "Wireless headphones add private listening and calls without using the tablet speakers.",
    ),
    (
        "laptops",
        "headphones",
        "A wireless headset adds private audio and clearer calls for work or entertainment.",
    ),
    (
        "speakers",
        "headphones",
        "Headphones add a private listening option alongside this speaker.",
    ),
    (
        "headphones",
        "speakers",
        "A speaker adds a room-listening option alongside these headphones.",
    ),
)


def upgrade() -> None:
    op.create_table(
        "catalog_category_relations",
        sa.Column("source_category", sa.String(length=40), nullable=False),
        sa.Column("target_category", sa.String(length=40), nullable=False),
        sa.Column(
            "relation_type",
            sa.String(length=40),
            server_default="POST_PURCHASE_CROSS_SELL",
            nullable=False,
        ),
        sa.Column("benefit", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "source_category <> target_category",
            name=op.f("ck_catalog_category_relations_different_categories"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_catalog_category_relations_sort_order_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["source_category"],
            ["catalog_categories.slug"],
            name=op.f("fk_catalog_category_relations_source_category_catalog_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_category"],
            ["catalog_categories.slug"],
            name=op.f("fk_catalog_category_relations_target_category_catalog_categories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "source_category",
            "target_category",
            "relation_type",
            name=op.f("pk_catalog_category_relations"),
        ),
        sa.UniqueConstraint(
            "source_category",
            "target_category",
            "relation_type",
            name="uq_catalog_category_relations_source_target_type",
        ),
    )
    op.create_index(
        "ix_catalog_category_relations_active_source",
        "catalog_category_relations",
        ["source_category", "relation_type", "is_active", "sort_order"],
    )

    relation_table = sa.table(
        "catalog_category_relations",
        sa.column("source_category", sa.String()),
        sa.column("target_category", sa.String()),
        sa.column("relation_type", sa.String()),
        sa.column("benefit", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        relation_table,
        [
            {
                "source_category": source,
                "target_category": target,
                "relation_type": "POST_PURCHASE_CROSS_SELL",
                "benefit": benefit,
                "is_active": True,
                "sort_order": index,
            }
            for index, (source, target, benefit) in enumerate(_RELATIONSHIPS)
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalog_category_relations_active_source",
        table_name="catalog_category_relations",
    )
    op.drop_table("catalog_category_relations")
