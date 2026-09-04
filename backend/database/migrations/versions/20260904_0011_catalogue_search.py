"""Make catalogue categories data-defined and add full-catalogue search indexes.

Revision ID: 20260904_0011
Revises: 20260904_0010
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0011"
down_revision: str | None = "20260904_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_CATEGORIES = (
    ("smartphones", "Smartphones", "Phones and mobile devices", ["phone", "phones", "mobile"]),
    ("speakers", "Speakers", "Portable, smart, home and soundbar speakers", ["speaker", "soundbar"]),
    ("headphones", "Headphones", "Headphones, headsets, earphones and earbuds", ["headphone", "earphone", "earbud", "headset"]),
    ("laptops", "Laptops", "Laptop and notebook computers", ["laptop", "notebook"]),
    ("tablets", "Tablets", "Tablet computers", ["tablet", "ipad"]),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "catalog_categories",
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "facet_definitions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'",
            name=op.f("ck_catalog_categories_slug_format"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_catalog_categories_sort_order_non_negative"),
        ),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_catalog_categories")),
    )
    op.create_index(
        "ix_catalog_categories_active_sort",
        "catalog_categories",
        ["is_active", "sort_order"],
    )

    category_table = sa.table(
        "catalog_categories",
        sa.column("slug", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("aliases", postgresql.JSONB()),
        sa.column("facet_definitions", postgresql.JSONB()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        category_table,
        [
            {
                "slug": slug,
                "display_name": display_name,
                "description": description,
                "aliases": aliases,
                "facet_definitions": [],
                "is_active": True,
                "sort_order": index,
            }
            for index, (slug, display_name, description, aliases) in enumerate(_LEGACY_CATEGORIES)
        ],
    )

    op.drop_index("ix_products_catalog_lookup", table_name="products")
    op.alter_column(
        "products",
        "category",
        existing_type=postgresql.ENUM(name="product_category"),
        type_=sa.String(length=40),
        postgresql_using="category::text",
        existing_nullable=False,
    )
    op.create_foreign_key(
        op.f("fk_products_category_catalog_categories"),
        "products",
        "catalog_categories",
        ["category"],
        ["slug"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "products",
        sa.Column("search_document", sa.Text(), server_default="", nullable=False),
    )
    op.execute(
        """
        UPDATE products
        SET search_document = lower(concat_ws(' ',
            sku, brand, model, category, title, description,
            search_tags::text, specifications::text
        ))
        """
    )
    op.execute(
        """
        CREATE FUNCTION shopy_products_search_document_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_document := lower(concat_ws(' ',
                NEW.sku, NEW.brand, NEW.model, NEW.category, NEW.title,
                NEW.description, NEW.search_tags::text, NEW.specifications::text
            ));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_products_search_document
        BEFORE INSERT OR UPDATE OF sku, brand, model, category, title, description,
            search_tags, specifications
        ON products
        FOR EACH ROW EXECUTE FUNCTION shopy_products_search_document_update()
        """
    )
    op.create_index("ix_products_catalog_lookup", "products", ["category", "is_active"])
    op.create_index(
        "ix_products_agent_filter",
        "products",
        ["is_active", "category", "offer_price_paise"],
    )
    op.execute(
        "CREATE INDEX ix_products_search_fts ON products "
        "USING gin (to_tsvector('simple', search_document))"
    )
    op.execute(
        "CREATE INDEX ix_products_search_trgm ON products "
        "USING gin (search_document gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_products_specifications_gin ON products "
        "USING gin (specifications jsonb_path_ops)"
    )
    op.execute("DROP TYPE IF EXISTS product_category")


def downgrade() -> None:
    connection = op.get_bind()
    unknown = connection.execute(
        sa.text(
            "SELECT DISTINCT category FROM products "
            "WHERE category NOT IN ('smartphones','speakers','headphones','laptops','tablets')"
        )
    ).scalars().all()
    if unknown:
        raise RuntimeError(
            "Cannot downgrade catalogue categories while non-legacy categories exist: "
            + ", ".join(str(value) for value in unknown)
        )

    op.execute("DROP TRIGGER IF EXISTS trg_products_search_document ON products")
    op.execute("DROP FUNCTION IF EXISTS shopy_products_search_document_update()")
    op.execute("DROP INDEX IF EXISTS ix_products_specifications_gin")
    op.execute("DROP INDEX IF EXISTS ix_products_search_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_search_fts")
    op.drop_index("ix_products_agent_filter", table_name="products")
    op.drop_index("ix_products_catalog_lookup", table_name="products")
    op.drop_constraint(
        op.f("fk_products_category_catalog_categories"),
        "products",
        type_="foreignkey",
    )
    legacy_enum = postgresql.ENUM(
        "smartphones",
        "speakers",
        "headphones",
        "laptops",
        "tablets",
        name="product_category",
    )
    legacy_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "products",
        "category",
        existing_type=sa.String(length=40),
        type_=legacy_enum,
        postgresql_using="category::product_category",
        existing_nullable=False,
    )
    op.drop_column("products", "search_document")
    op.create_index("ix_products_catalog_lookup", "products", ["category", "is_active"])
    op.drop_index("ix_catalog_categories_active_sort", table_name="catalog_categories")
    op.drop_table("catalog_categories")
