"""Create or adopt the pipeline-owned core catalog schema.

Revision ID: 20260922_00
Revises: None
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_00"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
) -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    """Create a complete fresh catalog while adopting bootstrap-created tables."""
    connection = op.get_bind()
    table_names = set(sa.inspect(connection).get_table_names())

    if "products" not in table_names:
        op.create_table(
            "products",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("brand", sa.String(length=64), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("family", sa.String(length=64), nullable=True),
            sa.Column("chip", sa.String(length=128), nullable=True),
            sa.Column("ram_gb", sa.Integer(), nullable=True),
            sa.Column("storage_gb", sa.Integer(), nullable=True),
            sa.Column("screen_size_inches", sa.Float(), nullable=True),
            sa.Column("gpu_model", sa.String(length=128), nullable=True),
            sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in (
        ("ix_products_category", ["category"]),
        ("ix_products_brand", ["brand"]),
        ("ix_products_family", ["family"]),
    ):
        _create_index_if_missing("products", index_name, columns)

    table_names = set(sa.inspect(connection).get_table_names())
    if "retailer_products" not in table_names:
        op.create_table(
            "retailer_products",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_product_id", sa.String(length=128), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("brand", sa.String(length=64), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column(
                "quality_status", sa.String(length=32), server_default="VALID", nullable=False
            ),
            sa.Column("quality_score", sa.Integer(), server_default="100", nullable=False),
            sa.Column("quality_flags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
            sa.Column(
                "lifecycle_status", sa.String(length=32), server_default="ACTIVE", nullable=False
            ),
            sa.Column("missed_crawls", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "first_seen_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["canonical_product_id"], ["products.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source", "source_product_id", name="uq_retailer_product_source_id"
            ),
        )

    for index_name, columns in (
        ("ix_retailer_products_canonical_product_id", ["canonical_product_id"]),
        ("ix_retailer_products_source", ["source"]),
        ("ix_retailer_products_source_product_id", ["source_product_id"]),
        ("ix_retailer_products_quality_status", ["quality_status"]),
        ("ix_retailer_products_lifecycle_status", ["lifecycle_status"]),
    ):
        _create_index_if_missing("retailer_products", index_name, columns)

    table_names = set(sa.inspect(connection).get_table_names())
    if "offers" not in table_names:
        op.create_table(
            "offers",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("retailer_product_id", sa.Integer(), nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_product_id", sa.String(length=128), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("price_paise", sa.BigInteger(), nullable=True),
            sa.Column("mrp_paise", sa.BigInteger(), nullable=True),
            sa.Column("coupon_price_paise", sa.BigInteger(), nullable=True),
            sa.Column("in_stock", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column(
                "quality_status", sa.String(length=32), server_default="VALID", nullable=False
            ),
            sa.Column(
                "lifecycle_status", sa.String(length=32), server_default="ACTIVE", nullable=False
            ),
            sa.Column("missed_crawls", sa.Integer(), server_default="0", nullable=False),
            sa.Column("seller", sa.String(length=128), nullable=True),
            sa.Column("rating", sa.Float(), nullable=True),
            sa.Column("review_count", sa.Integer(), nullable=True),
            sa.Column(
                "observed_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["canonical_product_id"], ["products.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["retailer_product_id"], ["retailer_products.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("retailer_product_id", name="uq_offer_retailer_product"),
        )

    for index_name, columns in (
        ("ix_offers_retailer_product_id", ["retailer_product_id"]),
        ("ix_offers_canonical_product_id", ["canonical_product_id"]),
        ("ix_offers_source", ["source"]),
        ("ix_offers_source_product_id", ["source_product_id"]),
        ("ix_offers_quality_status", ["quality_status"]),
        ("ix_offers_lifecycle_status", ["lifecycle_status"]),
    ):
        _create_index_if_missing("offers", index_name, columns)

    table_names = set(sa.inspect(connection).get_table_names())
    if "scrape_runs" not in table_names:
        op.create_table(
            "scrape_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), server_default="started", nullable=False),
            sa.Column("items_discovered", sa.Integer(), server_default="0", nullable=False),
            sa.Column("items_parsed", sa.Integer(), server_default="0", nullable=False),
            sa.Column("items_persisted", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_seconds", sa.Float(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("scrape_runs", "ix_scrape_runs_source", ["source"])

    table_names = set(sa.inspect(connection).get_table_names())
    if "product_identifiers" not in table_names:
        op.create_table(
            "product_identifiers",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=False),
            sa.Column("identifier_type", sa.String(length=32), nullable=False),
            sa.Column("identifier_value", sa.String(length=128), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["canonical_product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "identifier_type", "identifier_value", name="uq_identifier_type_value"
            ),
        )

    for index_name, columns in (
        ("ix_product_identifiers_canonical_product_id", ["canonical_product_id"]),
        ("ix_product_identifiers_identifier_type", ["identifier_type"]),
        ("ix_product_identifiers_identifier_value", ["identifier_value"]),
    ):
        _create_index_if_missing("product_identifiers", index_name, columns)


def downgrade() -> None:
    """Remove the core catalog in dependency order."""
    table_names = set(sa.inspect(op.get_bind()).get_table_names())
    for table_name in (
        "product_identifiers",
        "offers",
        "retailer_products",
        "scrape_runs",
        "products",
    ):
        if table_name in table_names:
            op.drop_table(table_name)
