"""Integration tests for managed pipeline schema migrations."""

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Table, inspect, text

from storage.engine import create_database_engine
from storage.migrations import current_database_revision, upgrade_database
from storage.models import Base, ProductModel


@pytest.mark.parametrize("retrieval_tables_exist", [False, True])
def test_retrieval_migration_creates_or_adopts_schema(
    tmp_path: Path,
    retrieval_tables_exist: bool,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    engine = create_database_engine(database_url)
    if retrieval_tables_exist:
        Base.metadata.create_all(engine)
    else:
        Base.metadata.create_all(engine, tables=[cast(Table, ProductModel.__table__)])
    engine.dispose()

    upgrade_database(database_url)
    upgrade_database(database_url)

    migrated_engine = create_database_engine(database_url)
    inspector = inspect(migrated_engine)
    table_names = set(inspector.get_table_names())
    document_indexes = {index["name"] for index in inspector.get_indexes("product_documents")}
    embedding_indexes = {index["name"] for index in inspector.get_indexes("product_embeddings")}
    migrated_engine.dispose()

    assert {
        "products",
        "product_documents",
        "product_embeddings",
        "product_import_runs",
        "alembic_version_pipeline",
    } <= table_names
    assert "scrape_runs" not in table_names
    assert "ix_product_documents_category" in document_indexes
    assert "ix_product_embeddings_content_hash" in embedding_indexes
    assert current_database_revision(database_url) == "20261002_03"


def test_managed_migrations_create_complete_catalog_from_empty_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'fresh-pipeline.db'}"

    upgrade_database(database_url)

    engine = create_database_engine(database_url)
    table_names = set(inspect(engine).get_table_names())
    engine.dispose()

    assert {
        "products",
        "retailer_products",
        "offers",
        "product_identifiers",
        "product_documents",
        "product_embeddings",
        "product_import_runs",
        "alembic_version_pipeline",
    } <= table_names


def test_unused_scrape_run_migration_preserves_unexpected_history(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'scrape-history.db'}"
    upgrade_database(database_url, revision="20260920_02")

    engine = create_database_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scrape_runs (id, source, category) "
                "VALUES ('run-1', 'amazon', 'laptop')"
            )
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="archive its telemetry"):
        upgrade_database(database_url)

    verification_engine = create_database_engine(database_url)
    with verification_engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM scrape_runs")).scalar_one() == "run-1"
    verification_engine.dispose()
