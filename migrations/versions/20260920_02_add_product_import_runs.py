"""Add durable product URL import runs.

Revision ID: 20260920_02
Revises: 20260920_01
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_02"
down_revision: str | None = "20260920_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_index_if_missing(
    inspector: sa.Inspector,
    index_name: str,
    columns: list[str],
) -> None:
    existing_indexes = {index["name"] for index in inspector.get_indexes("product_import_runs")}
    if index_name not in existing_indexes:
        op.create_index(index_name, "product_import_runs", columns, unique=False)


def upgrade() -> None:
    """Create or adopt the pipeline-owned import-run status table."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "product_import_runs" not in set(inspector.get_table_names()):
        op.create_table(
            "product_import_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_product_id", sa.String(length=128), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("progress_stage", sa.String(length=32), nullable=False),
            sa.Column("canonical_product_id", sa.String(length=64), nullable=True),
            sa.Column(
                "reused_fresh_listing",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column("failure_code", sa.String(length=64), nullable=True),
            sa.Column("failure_message", sa.String(length=500), nullable=True),
            sa.Column(
                "requested_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["canonical_product_id"],
                ["products.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    for index_name, columns in (
        ("ix_product_import_runs_source", ["source"]),
        ("ix_product_import_runs_source_product_id", ["source_product_id"]),
        ("ix_product_import_runs_category", ["category"]),
        ("ix_product_import_runs_status", ["status"]),
        ("ix_product_import_runs_canonical_product_id", ["canonical_product_id"]),
    ):
        _create_index_if_missing(sa.inspect(connection), index_name, columns)


def downgrade() -> None:
    """Remove only the pipeline-owned product import run table."""
    if "product_import_runs" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("product_import_runs")
