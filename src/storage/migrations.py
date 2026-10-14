"""Programmatic Alembic entry points for deployment commands and tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from storage.engine import create_database_engine


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_alembic_config(database_url: str | None = None) -> Config:
    """Build an Alembic config rooted at the source checkout or container app directory."""
    root = _project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str | None = None, revision: str = "head") -> None:
    """Apply managed database migrations through the requested revision."""
    command.upgrade(build_alembic_config(database_url), revision)


def current_database_revision(database_url: str | None = None) -> str | None:
    """Return the current Alembic revision without changing the schema."""
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(
                connection,
                opts={"version_table": "alembic_version_pipeline"},
            ).get_current_revision()
    finally:
        engine.dispose()
