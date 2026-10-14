import json

import httpx
import pytest
from pydantic import ValidationError

from retrieval.embeddings import (
    EmbeddingProviderError,
    EmbeddingVector,
    OllamaEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    validate_embedding_batch,
)


def test_embedding_batch_validates_count_and_dimensions() -> None:
    vectors = [EmbeddingVector(values=[0.1, 0.2, 0.3])]
    validate_embedding_batch(vectors, expected_count=1, expected_dimensions=3)

    with pytest.raises(ValueError, match="unexpected vector count"):
        validate_embedding_batch(vectors, expected_count=2, expected_dimensions=3)
    with pytest.raises(ValueError, match="unexpected vector dimensions"):
        validate_embedding_batch(vectors, expected_count=1, expected_dimensions=2)


def test_embedding_values_must_be_finite() -> None:
    with pytest.raises(ValidationError):
        EmbeddingVector(values=[float("nan")])


def test_openai_compatible_provider_validates_and_orders_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://embeddings.example/v1/embeddings"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload == {
            "input": ["first", "second"],
            "model": "embed-test",
            "encoding_format": "float",
            "dimensions": 3,
        }
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "embed-test",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]},
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleEmbeddingProvider(
        provider_name="example",
        model_name="embed-test",
        dimensions=3,
        base_url="https://embeddings.example/v1/",
        api_key="test-key",
        client=client,
    )

    vectors = provider.embed(["first", "second"])

    assert [vector.values for vector in vectors] == [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
    ]
    client.close()


def test_openrouter_embedding_uses_document_input_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input_type"] == "search_document"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "qwen/qwen3-embedding-8b",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleEmbeddingProvider(
        provider_name="openrouter",
        model_name="qwen/qwen3-embedding-8b",
        dimensions=2,
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        client=client,
    )

    vectors = provider.embed(["stable product facts"])

    assert vectors[0].values == [0.1, 0.2]
    client.close()


def test_openai_compatible_provider_rejects_wrong_dimensions() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "embed-test",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleEmbeddingProvider(
        provider_name="example",
        model_name="embed-test",
        dimensions=3,
        base_url="https://embeddings.example/v1",
        client=client,
    )

    with pytest.raises(EmbeddingProviderError, match="failed validation"):
        provider.embed(["product"])
    client.close()


def test_ollama_provider_validates_native_embed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "http://localhost:11434/api/embed"
        assert payload == {
            "model": "bge-m3",
            "input": ["first", "second"],
            "truncate": False,
            "dimensions": 3,
        }
        return httpx.Response(
            200,
            json={
                "model": "bge-m3",
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        provider_name="ollama",
        model_name="bge-m3",
        dimensions=3,
        base_url="http://localhost:11434/",
        client=client,
    )

    vectors = provider.embed(["first", "second"])

    assert [vector.values for vector in vectors] == [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
    ]
    client.close()
