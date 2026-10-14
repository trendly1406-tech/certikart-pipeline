from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from retrieval.documents import build_canonical_product_document
from retrieval.embeddings import EmbeddingVector
from retrieval.repository import ProductRetrievalRepository
from storage.engine import init_db
from storage.models import ProductEmbeddingModel, ProductModel


def test_document_and_embedding_upserts_are_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        product = ProductModel(
            id="clus_test",
            category="laptop",
            brand="Example",
            model_name="Model 15",
            ram_gb=16,
            attributes={"operating_system": "Windows 11"},
        )
        session.add(product)
        session.flush()

        repository = ProductRetrievalRepository(session)
        document = repository.upsert_document(build_canonical_product_document(product))
        first_id = document.id
        same_document = repository.upsert_document(build_canonical_product_document(product))
        assert same_document.id == first_id

        first_embedding = repository.upsert_embedding(
            document=document,
            provider="test",
            model_name="test-3d",
            vector=EmbeddingVector(values=[0.1, 0.2, 0.3]),
        )
        second_embedding = repository.upsert_embedding(
            document=document,
            provider="test",
            model_name="test-3d",
            vector=EmbeddingVector(values=[0.4, 0.5, 0.6]),
        )
        assert second_embedding.id == first_embedding.id
        assert session.scalar(select(ProductEmbeddingModel)) is not None
    engine.dispose()
