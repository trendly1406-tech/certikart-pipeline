"""Database engine initialization and session management."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from storage.models import Base


def create_database_engine(url: str | None = None) -> Engine:
    """Create a configured SQLAlchemy Engine."""
    database_url = url or get_settings().database_url.get_secret_value()
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


def init_db(engine: Engine) -> None:
    """Bootstrap local/test schemas; migration-managed deployments make no DDL changes."""
    if get_settings().schema_management == "migrations":
        return
    if "postgres" in str(engine.url):
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=engine)
    if "postgres" in str(engine.url):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE retailer_products "
                    "ADD COLUMN IF NOT EXISTS quality_status VARCHAR(32) DEFAULT 'VALID' NOT NULL, "
                    "ADD COLUMN IF NOT EXISTS quality_score INTEGER DEFAULT 100 NOT NULL, "
                    "ADD COLUMN IF NOT EXISTS quality_flags JSON DEFAULT '[]'::json NOT NULL;"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE offers "
                    "ADD COLUMN IF NOT EXISTS quality_status VARCHAR(32) DEFAULT 'VALID' NOT NULL;"
                )
            )


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a thread-safe sessionmaker factory."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
