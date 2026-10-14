"""Provider-neutral embedding contracts and OpenAI-compatible adapter."""

from __future__ import annotations

import math
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config import Settings


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider response cannot be safely used."""


class EmbeddingVector(BaseModel):
    values: list[float] = Field(min_length=1)

    @field_validator("values")
    @classmethod
    def finite_values(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding values must be finite")
        return values


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[EmbeddingVector]: ...


class _EmbeddingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class _EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[_EmbeddingItem]
    model: str


class _OllamaEmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str
    embeddings: list[list[float]]


class OpenAICompatibleEmbeddingProvider:
    """Generate vectors through an OpenAI-compatible ``POST /embeddings`` API."""

    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        dimensions: int,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.dimensions = dimensions
        self._endpoint = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._client = client

    def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text")

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "input": texts,
            "model": self.model_name,
            "encoding_format": "float",
            "dimensions": self.dimensions,
        }
        if self.provider_name.casefold() == "openrouter":
            payload["input_type"] = "search_document"
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            response = client.post(self._endpoint, headers=headers, json=payload)
            response.raise_for_status()
            parsed = _EmbeddingResponse.model_validate(response.json())
            ordered = sorted(parsed.data, key=lambda item: item.index)
            if [item.index for item in ordered] != list(range(len(texts))):
                raise EmbeddingProviderError("Embedding provider returned invalid indexes")
            vectors = [EmbeddingVector(values=item.embedding) for item in ordered]
            validate_embedding_batch(
                vectors,
                expected_count=len(texts),
                expected_dimensions=self.dimensions,
            )
            return vectors
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise EmbeddingProviderError("Embedding provider request failed validation") from exc
        finally:
            if owns_client:
                client.close()


class OllamaEmbeddingProvider:
    """Generate vectors through Ollama's native ``POST /api/embed`` API."""

    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        dimensions: int,
        base_url: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.dimensions = dimensions
        self._endpoint = f"{base_url.rstrip('/')}/api/embed"
        self._timeout_seconds = timeout_seconds
        self._client = client

    def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding input must contain non-empty text")

        payload = {
            "model": self.model_name,
            "input": texts,
            "truncate": False,
            "dimensions": self.dimensions,
        }
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            response = client.post(self._endpoint, json=payload)
            response.raise_for_status()
            parsed = _OllamaEmbeddingResponse.model_validate(response.json())
            vectors = [EmbeddingVector(values=values) for values in parsed.embeddings]
            validate_embedding_batch(
                vectors,
                expected_count=len(texts),
                expected_dimensions=self.dimensions,
            )
            return vectors
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise EmbeddingProviderError("Embedding provider request failed validation") from exc
        finally:
            if owns_client:
                client.close()


def build_configured_embedding_provider(
    settings: Settings,
) -> EmbeddingProvider:
    """Build the configured adapter without making a provider request."""
    if not settings.embedding_generation_enabled:
        raise ValueError("Embedding generation is disabled")
    if not settings.embedding_provider or not settings.embedding_model:
        raise ValueError("Configure an embedding provider and model")
    if settings.embedding_api_format == "ollama":
        return OllamaEmbeddingProvider(
            provider_name=settings.embedding_provider,
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            base_url=str(settings.embedding_base_url),
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    return OpenAICompatibleEmbeddingProvider(
        provider_name=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        base_url=str(settings.embedding_base_url),
        api_key=settings.embedding_api_key.get_secret_value(),
        timeout_seconds=settings.embedding_timeout_seconds,
    )


def validate_embedding_batch(
    vectors: list[EmbeddingVector],
    *,
    expected_count: int,
    expected_dimensions: int,
) -> None:
    if len(vectors) != expected_count:
        raise ValueError("Embedding provider returned an unexpected vector count")
    if any(len(vector.values) != expected_dimensions for vector in vectors):
        raise ValueError("Embedding provider returned unexpected vector dimensions")
