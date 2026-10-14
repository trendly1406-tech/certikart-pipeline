import pytest
from pydantic import ValidationError

from config import Settings


def test_settings_use_safe_pipeline_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.obey_robots_txt is True
    assert settings.max_source_concurrency == 2
    assert settings.collection_profile == "smoke"
    assert settings.schema_management == "bootstrap"
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert settings.embedding_generation_enabled is False
    assert settings.embedding_api_format == "openai"
    assert str(settings.embedding_base_url) == "https://api.openai.com/v1"


def test_openai_embedding_generation_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="requires an API key"):
        Settings(
            _env_file=None,
            embedding_generation_enabled=True,
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
        )

    settings = Settings(
        _env_file=None,
        embedding_generation_enabled=True,
        embedding_provider="openai-compatible",
        embedding_model="local-embedding-model",
        embedding_base_url="http://localhost:11434/v1",
    )
    assert settings.embedding_api_key.get_secret_value() == ""

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            schema_management="migrations",
            embedding_generation_enabled=True,
            embedding_provider="openai-compatible",
            embedding_model="local-embedding-model",
            embedding_base_url="http://embeddings.internal/v1",
        )


def test_ollama_embeddings_are_keyless_and_deepseek_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        embedding_generation_enabled=True,
        embedding_api_format="ollama",
        embedding_provider="ollama",
        embedding_model="bge-m3",
        embedding_base_url="http://localhost:11434",
        embedding_dimensions=1024,
    )
    assert settings.embedding_api_key.get_secret_value() == ""
    assert settings.embedding_dimensions == 1024

    with pytest.raises(ValidationError, match="does not expose an embeddings API"):
        Settings(
            _env_file=None,
            embedding_generation_enabled=True,
            embedding_provider="deepseek",
            embedding_model="deepseek-chat",
            embedding_api_key="test-only",
            embedding_base_url="https://api.deepseek.com",
        )


def test_production_requires_migration_managed_schema() -> None:
    with pytest.raises(ValidationError, match="migration-managed database schemas"):
        Settings(_env_file=None, environment="production")

    settings = Settings(
        _env_file=None,
        environment="production",
        schema_management="migrations",
    )
    assert settings.schema_management == "migrations"
