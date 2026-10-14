"""Idempotent catalog document synchronization and embedding backfill."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from retrieval.documents import DOCUMENT_VERSION, build_canonical_product_document
from retrieval.embeddings import EmbeddingProvider, validate_embedding_batch
from retrieval.repository import ProductRetrievalRepository


class EmbeddingIndexHealth(BaseModel):
    provider: str
    model_name: str
    dimensions: int = Field(gt=0)
    category: str | None = None
    total_products: int = Field(ge=0)
    total_documents: int = Field(ge=0)
    missing_documents: int = Field(ge=0)
    current_embeddings: int = Field(ge=0)
    missing_embeddings: int = Field(ge=0)
    stale_embeddings: int = Field(ge=0)
    coverage_percent: float = Field(ge=0, le=100)
    ready: bool


class EmbeddingBackfillResult(BaseModel):
    provider: str
    model_name: str
    dimensions: int = Field(gt=0)
    category: str | None = None
    documents_created_or_refreshed: int = Field(ge=0)
    embeddings_written: int = Field(ge=0)
    batches_completed: int = Field(ge=0)
    health: EmbeddingIndexHealth


def get_embedding_index_health(
    session: Session,
    *,
    provider: str,
    model_name: str,
    dimensions: int,
    category: str | None = None,
) -> EmbeddingIndexHealth:
    values = ProductRetrievalRepository(session).index_health(
        provider=provider,
        model_name=model_name,
        dimensions=dimensions,
        category=category,
    )
    total_products = values["total_products"]
    coverage_percent = (
        100.0
        if total_products == 0
        else round((values["current_embeddings"] / total_products) * 100, 2)
    )
    ready = values["missing_documents"] == 0 and (
        values["missing_embeddings"] == 0 and values["stale_embeddings"] == 0
    )
    return EmbeddingIndexHealth(
        provider=provider,
        model_name=model_name,
        dimensions=dimensions,
        category=category,
        coverage_percent=coverage_percent,
        ready=ready,
        **values,
    )


def backfill_product_embeddings(
    session: Session,
    *,
    provider: EmbeddingProvider,
    batch_size: int,
    limit: int,
    category: str | None = None,
) -> EmbeddingBackfillResult:
    """Synchronize missing documents and resume provider/model-specific embeddings."""
    if batch_size < 1 or limit < 1:
        raise ValueError("Embedding batch size and limit must be positive")

    repository = ProductRetrievalRepository(session)
    products = repository.list_products_needing_documents(
        document_version=DOCUMENT_VERSION,
        category=category,
    )
    for product in products:
        repository.upsert_document(build_canonical_product_document(product))
    if products:
        session.commit()

    documents = repository.list_documents_needing_embedding(
        provider=provider.provider_name,
        model_name=provider.model_name,
        dimensions=provider.dimensions,
        limit=limit,
        category=category,
    )
    batches_completed = 0
    embeddings_written = 0
    for offset in range(0, len(documents), batch_size):
        batch = documents[offset : offset + batch_size]
        vectors = provider.embed([document.document_text for document in batch])
        validate_embedding_batch(
            vectors,
            expected_count=len(batch),
            expected_dimensions=provider.dimensions,
        )
        for document, vector in zip(batch, vectors, strict=True):
            repository.upsert_embedding(
                document=document,
                provider=provider.provider_name,
                model_name=provider.model_name,
                vector=vector,
            )
        session.commit()
        batches_completed += 1
        embeddings_written += len(batch)

    health = get_embedding_index_health(
        session,
        provider=provider.provider_name,
        model_name=provider.model_name,
        dimensions=provider.dimensions,
        category=category,
    )
    return EmbeddingBackfillResult(
        provider=provider.provider_name,
        model_name=provider.model_name,
        dimensions=provider.dimensions,
        category=category,
        documents_created_or_refreshed=len(products),
        embeddings_written=embeddings_written,
        batches_completed=batches_completed,
        health=health,
    )
