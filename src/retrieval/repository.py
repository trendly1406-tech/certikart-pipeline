"""Idempotent storage for canonical documents and provider-versioned embeddings."""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from retrieval.documents import CanonicalProductDocument
from retrieval.embeddings import EmbeddingVector
from storage.models import (
    ProductDocumentModel,
    ProductEmbeddingModel,
    ProductModel,
    utc_now,
)


class ProductRetrievalRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_document(self, document: CanonicalProductDocument) -> ProductDocumentModel:
        existing = self.session.scalar(
            select(ProductDocumentModel).where(
                ProductDocumentModel.canonical_product_id == document.canonical_product_id
            )
        )
        if existing is None:
            existing = ProductDocumentModel(
                canonical_product_id=document.canonical_product_id,
                category=document.category,
                document_text=document.document_text,
                content_hash=document.content_hash,
                document_version=document.document_version,
                source_fields=document.source_fields,
            )
            self.session.add(existing)
        elif (
            existing.content_hash != document.content_hash
            or existing.document_version != document.document_version
        ):
            existing.category = document.category
            existing.document_text = document.document_text
            existing.content_hash = document.content_hash
            existing.document_version = document.document_version
            existing.source_fields = document.source_fields
            existing.updated_at = utc_now()
        self.session.flush()
        return existing

    def upsert_embedding(
        self,
        *,
        document: ProductDocumentModel,
        provider: str,
        model_name: str,
        vector: EmbeddingVector,
    ) -> ProductEmbeddingModel:
        existing = self.session.scalar(
            select(ProductEmbeddingModel).where(
                ProductEmbeddingModel.product_document_id == document.id,
                ProductEmbeddingModel.provider == provider,
                ProductEmbeddingModel.model_name == model_name,
            )
        )
        values = vector.values
        if existing is None:
            existing = ProductEmbeddingModel(
                canonical_product_id=document.canonical_product_id,
                product_document_id=document.id,
                provider=provider,
                model_name=model_name,
                dimensions=len(values),
                embedding=values,
                content_hash=document.content_hash,
            )
            self.session.add(existing)
        elif existing.content_hash != document.content_hash or existing.dimensions != len(values):
            existing.canonical_product_id = document.canonical_product_id
            existing.dimensions = len(values)
            existing.embedding = values
            existing.content_hash = document.content_hash
            existing.updated_at = utc_now()
        self.session.flush()
        return existing

    def list_products_needing_documents(
        self,
        *,
        document_version: str,
        category: str | None = None,
    ) -> list[ProductModel]:
        join_condition = ProductDocumentModel.canonical_product_id == ProductModel.id
        stmt = (
            select(ProductModel)
            .outerjoin(ProductDocumentModel, join_condition)
            .where(
                or_(
                    ProductDocumentModel.id.is_(None),
                    ProductDocumentModel.document_version != document_version,
                )
            )
            .order_by(ProductModel.id)
        )
        if category:
            stmt = stmt.where(ProductModel.category == category)
        return list(self.session.scalars(stmt).all())

    def list_documents_needing_embedding(
        self,
        *,
        provider: str,
        model_name: str,
        dimensions: int,
        limit: int,
        category: str | None = None,
    ) -> list[ProductDocumentModel]:
        join_condition = and_(
            ProductEmbeddingModel.product_document_id == ProductDocumentModel.id,
            ProductEmbeddingModel.provider == provider,
            ProductEmbeddingModel.model_name == model_name,
        )
        stmt = (
            select(ProductDocumentModel)
            .outerjoin(ProductEmbeddingModel, join_condition)
            .where(
                or_(
                    ProductEmbeddingModel.id.is_(None),
                    ProductEmbeddingModel.content_hash != ProductDocumentModel.content_hash,
                    ProductEmbeddingModel.dimensions != dimensions,
                )
            )
            .order_by(ProductDocumentModel.id)
            .limit(limit)
        )
        if category:
            stmt = stmt.where(ProductDocumentModel.category == category)
        return list(self.session.scalars(stmt).all())

    def index_health(
        self,
        *,
        provider: str,
        model_name: str,
        dimensions: int,
        category: str | None = None,
    ) -> dict[str, int]:
        product_stmt = select(func.count(ProductModel.id))
        document_stmt = select(func.count(ProductDocumentModel.id))
        if category:
            product_stmt = product_stmt.where(ProductModel.category == category)
            document_stmt = document_stmt.where(ProductDocumentModel.category == category)

        provider_match = and_(
            ProductEmbeddingModel.product_document_id == ProductDocumentModel.id,
            ProductEmbeddingModel.provider == provider,
            ProductEmbeddingModel.model_name == model_name,
        )
        embedded_stmt = (
            select(func.count(ProductEmbeddingModel.id))
            .select_from(ProductDocumentModel)
            .join(ProductEmbeddingModel, provider_match)
        )
        current_stmt = embedded_stmt.where(
            ProductEmbeddingModel.content_hash == ProductDocumentModel.content_hash,
            ProductEmbeddingModel.dimensions == dimensions,
        )
        if category:
            embedded_stmt = embedded_stmt.where(ProductDocumentModel.category == category)
            current_stmt = current_stmt.where(ProductDocumentModel.category == category)

        total_products = int(self.session.scalar(product_stmt) or 0)
        total_documents = int(self.session.scalar(document_stmt) or 0)
        provider_embeddings = int(self.session.scalar(embedded_stmt) or 0)
        current_embeddings = int(self.session.scalar(current_stmt) or 0)
        return {
            "total_products": total_products,
            "total_documents": total_documents,
            "missing_documents": max(total_products - total_documents, 0),
            "current_embeddings": current_embeddings,
            "missing_embeddings": max(total_documents - provider_embeddings, 0),
            "stale_embeddings": max(provider_embeddings - current_embeddings, 0),
        }
