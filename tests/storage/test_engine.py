"""Tests for database engine and session factory."""

import pytest
from sqlalchemy import inspect

from config import Settings
from storage.engine import create_database_engine, get_session_factory, init_db
from storage.models import Base


def test_create_database_engine_sqlite() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    assert engine is not None
    init_db(engine)
    assert len(Base.metadata.tables) >= 5

    factory = get_session_factory(engine)
    session = factory()
    assert session is not None
    session.close()
    engine.dispose()


def test_init_db_skips_ddl_in_migration_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "storage.engine.get_settings",
        lambda: Settings(schema_management="migrations"),
    )
    engine = create_database_engine("sqlite:///:memory:")

    init_db(engine)

    assert inspect(engine).get_table_names() == []
    engine.dispose()
