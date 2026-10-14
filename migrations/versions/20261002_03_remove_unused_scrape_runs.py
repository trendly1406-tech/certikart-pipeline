"""Remove the unused scrape telemetry table.

Revision ID: 20261002_03
Revises: 20260920_02
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_03"
down_revision: str | None = "20260920_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop scrape_runs only when it contains no history to lose."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "scrape_runs" not in set(inspector.get_table_names()):
        return

    row_count = connection.execute(sa.text("SELECT COUNT(*) FROM scrape_runs")).scalar_one()
    if row_count:
        raise RuntimeError(
            "Refusing to drop non-empty scrape_runs; archive its telemetry before upgrading."
        )
    op.drop_table("scrape_runs")


def downgrade() -> None:
    """Restore the empty legacy table shape for migration rollback."""
    if "scrape_runs" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
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
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scrape_runs_source", "scrape_runs", ["source"], unique=False)
