"""Alembic migration environment for the pipeline catalog."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import get_settings
from storage.models import Base

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured_url = (alembic_config.get_main_option("sqlalchemy.url") or "").strip()
    if configured_url:
        return configured_url
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Reject offline rendering because baseline adoption requires live inspection."""
    raise RuntimeError("Pipeline migrations require an online database connection")


def run_migrations_online() -> None:
    """Run migrations with one transactional database connection."""
    section = alembic_config.get_section(alembic_config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_pipeline",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
