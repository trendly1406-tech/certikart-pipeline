"""Validated pipeline configuration."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CERTIKART_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://certikart:certikart_dev_only@localhost:5432/certikart"
    )
    schema_management: Literal["bootstrap", "migrations"] = "bootstrap"
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_source_concurrency: int = Field(default=2, ge=1, le=10)
    obey_robots_txt: bool = True
    collection_profile: Literal["smoke", "shadow", "incremental", "backfill"] = "smoke"
    image_upload_enabled: bool = False
    image_bucket: str = Field(default="", pattern=r"^$|^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    image_supabase_url: AnyHttpUrl | None = None
    image_supabase_secret_key: SecretStr = SecretStr("")
    image_s3_bucket: str = Field(default="", pattern=r"^$|^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    image_s3_region: str = Field(default="ap-south-1", pattern=r"^[a-z0-9-]+$")
    image_s3_access_key_id: SecretStr = SecretStr("")
    image_s3_secret_access_key: SecretStr = SecretStr("")
    image_s3_session_token: SecretStr = SecretStr("")
    image_s3_endpoint_url: AnyHttpUrl | None = None
    image_s3_force_path_style: bool = False
    image_public_base_url: AnyHttpUrl | None = None
    image_s3_prefix: str = Field(default="products", pattern=r"^[a-zA-Z0-9/_-]+$")
    image_max_bytes: int = Field(default=8_000_000, ge=1024, le=20_000_000)
    image_max_dimension: int = Field(default=1200, ge=200, le=2400)
    image_max_per_product: int = Field(default=4, ge=1, le=6)
    embedding_generation_enabled: bool = False
    embedding_api_format: Literal["openai", "ollama"] = "openai"
    embedding_provider: str = Field(default="", max_length=64, pattern=r"^$|^[a-zA-Z0-9._-]+$")
    embedding_model: str = Field(default="", max_length=128, pattern=r"^$|^[a-zA-Z0-9._:/-]+$")
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    embedding_dimensions: int = Field(default=1536, ge=1, le=8192)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    semantic_search_enabled: bool = False
    image_allowed_hosts: tuple[str, ...] = (
        "m.media-amazon.com",
        "images-eu.ssl-images-amazon.com",
        "rukminim2.flixcart.com",
        "rukmini1.flixcart.com",
        "media.croma.com",
        "media.tatacroma.com",
    )

    @field_validator(
        "image_public_base_url",
        "image_s3_endpoint_url",
        "image_supabase_url",
        mode="before",
    )
    @classmethod
    def empty_image_base_url(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_image_storage(self) -> Self:
        if self.environment == "production" and self.schema_management != "migrations":
            raise ValueError("Production requires migration-managed database schemas")
        if bool(self.image_s3_access_key_id.get_secret_value()) != bool(
            self.image_s3_secret_access_key.get_secret_value()
        ):
            raise ValueError("Configure both image S3 access key and secret, or use an IAM role")
        if self.image_upload_enabled and not self.image_storage_bucket:
            raise ValueError("Image uploads require a storage bucket")
        if self.image_supabase_url and not self.image_supabase_secret_key.get_secret_value():
            raise ValueError("Supabase image storage requires a server-side secret key")
        if self.image_public_base_url and (
            self.image_public_base_url.scheme != "https"
            or self.image_public_base_url.username
            or self.image_public_base_url.query
            or self.image_public_base_url.fragment
        ):
            raise ValueError(
                "Image public base URL must be HTTPS without credentials/query/fragment"
            )
        if self.image_s3_endpoint_url and (
            self.image_s3_endpoint_url.scheme != "https"
            or self.image_s3_endpoint_url.username
            or self.image_s3_endpoint_url.query
            or self.image_s3_endpoint_url.fragment
        ):
            raise ValueError(
                "Image S3 endpoint URL must be HTTPS without credentials/query/fragment"
            )
        if self.embedding_generation_enabled and (
            not self.embedding_provider or not self.embedding_model
        ):
            raise ValueError("Embedding generation requires an embedding provider and model")
        if (
            self.embedding_generation_enabled
            and self.embedding_provider.casefold() in {"openai", "openrouter"}
            and not self.embedding_api_key.get_secret_value()
        ):
            raise ValueError("Cloud embedding generation requires an API key")
        if self.embedding_generation_enabled and (
            self.embedding_provider.casefold() == "deepseek"
            or self.embedding_base_url.host in {"api.deepseek.com", "api.deepseek.com.cn"}
        ):
            raise ValueError(
                "DeepSeek does not expose an embeddings API; configure a dedicated "
                "embedding model such as Ollama bge-m3"
            )
        if self.embedding_generation_enabled and (
            self.embedding_base_url.username
            or self.embedding_base_url.query
            or self.embedding_base_url.fragment
        ):
            raise ValueError("Embedding base URL cannot contain credentials, query, or fragment")
        if (
            self.embedding_generation_enabled
            and self.environment == "production"
            and self.embedding_base_url.scheme != "https"
        ):
            raise ValueError("Production embedding base URL must use HTTPS")
        return self

    @property
    def image_storage_bucket(self) -> str:
        """Prefer the provider-neutral bucket while accepting the legacy S3 name."""
        return self.image_bucket or self.image_s3_bucket

    @property
    def image_delivery_base_url(self) -> AnyHttpUrl:
        """Use direct S3 delivery unless a custom base URL was explicitly configured."""
        if self.image_public_base_url is not None:
            return self.image_public_base_url
        if self.image_supabase_url is not None:
            if not self.image_storage_bucket:
                raise ValueError("Configure an image storage bucket")
            project_url = str(self.image_supabase_url).rstrip("/")
            return AnyHttpUrl(f"{project_url}/storage/v1/object/public/{self.image_storage_bucket}")
        if not self.image_storage_bucket:
            raise ValueError("Configure an image S3 bucket")
        suffix = "amazonaws.com.cn" if self.image_s3_region.startswith("cn-") else "amazonaws.com"
        endpoint = f"s3.{self.image_s3_region}.{suffix}"
        # Dotted bucket names cannot use the wildcard HTTPS certificate for virtual hosting.
        if "." in self.image_storage_bucket:
            return AnyHttpUrl(f"https://{endpoint}/{self.image_storage_bucket}")
        return AnyHttpUrl(f"https://{self.image_storage_bucket}.{endpoint}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
