"""Add semantic product documents and pgvector embeddings.

Revision ID: 20260920_01
Revises: 20260922_00
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260920_01"
down_revision: str | None = "20260922_00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
) -> None:
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    """Create retrieval storage alongside the existing canonical products table."""
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())

    if "product_documents" not in table_names:
        op.create_table(
            "product_documents",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("document_text", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("document_version", sa.String(length=32), nullable=False),
            sa.Column("source_fields", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["canonical_product_id"],
                ["products.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "canonical_product_id",
                name="uq_product_document_product",
            ),
        )

    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_documents",
        "ix_product_documents_canonical_product_id",
        ["canonical_product_id"],
    )
    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_documents",
        "ix_product_documents_category",
        ["category"],
    )
    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_documents",
        "ix_product_documents_content_hash",
        ["content_hash"],
    )

    inspector = sa.inspect(connection)
    if "product_embeddings" not in set(inspector.get_table_names()):
        op.create_table(
            "product_embeddings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=False),
            sa.Column("product_document_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model_name", sa.String(length=128), nullable=False),
            sa.Column("dimensions", sa.Integer(), nullable=False),
            sa.Column("embedding", Vector(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["canonical_product_id"],
                ["products.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["product_document_id"],
                ["product_documents.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "product_document_id",
                "provider",
                "model_name",
                name="uq_product_embedding_document_provider_model",
            ),
        )

    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_embeddings",
        "ix_product_embeddings_canonical_product_id",
        ["canonical_product_id"],
    )
    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_embeddings",
        "ix_product_embeddings_product_document_id",
        ["product_document_id"],
    )
    inspector = sa.inspect(connection)
    _create_index_if_missing(
        inspector,
        "product_embeddings",
        "ix_product_embeddings_content_hash",
        ["content_hash"],
    )


def downgrade() -> None:
    """Remove only the retrieval tables; keep catalog data and vector extension."""
    table_names = set(sa.inspect(op.get_bind()).get_table_names())
    if "product_embeddings" in table_names:
        op.drop_table("product_embeddings")
    if "product_documents" in table_names:
        op.drop_table("product_documents")
