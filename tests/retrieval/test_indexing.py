from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from retrieval.documents import build_canonical_product_document
from retrieval.embeddings import EmbeddingVector
from retrieval.indexing import backfill_product_embeddings, get_embedding_index_health
from retrieval.repository import ProductRetrievalRepository
from storage.engine import init_db
from storage.models import ProductModel


class FakeEmbeddingProvider:
    provider_name = "test"
    model_name = "test-3d"
    dimensions = 3

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[EmbeddingVector]:
        self.calls.append(texts)
        return [EmbeddingVector(values=[float(index), 0.5, 1.0]) for index, _ in enumerate(texts)]


def _product(product_id: str, *, category: str = "laptop") -> ProductModel:
    return ProductModel(
        id=product_id,
        category=category,
        brand="Example",
        model_name=f"Model {product_id}",
        ram_gb=16,
        attributes={"operating_system": "Test OS"},
    )


def test_backfill_is_resumable_and_reports_coverage() -> None:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    provider = FakeEmbeddingProvider()
    with Session(engine) as session:
        session.add_all([_product("clus_a"), _product("clus_b")])
        session.commit()

        first = backfill_product_embeddings(
            session,
            provider=provider,
            batch_size=1,
            limit=1,
        )
        assert first.documents_created_or_refreshed == 2
        assert first.embeddings_written == 1
        assert first.health.current_embeddings == 1
        assert first.health.coverage_percent == 50.0
        assert first.health.ready is False

        second = backfill_product_embeddings(
            session,
            provider=provider,
            batch_size=2,
            limit=10,
        )
        assert second.documents_created_or_refreshed == 0
        assert second.embeddings_written == 1
        assert second.health.coverage_percent == 100.0
        assert second.health.ready is True

        third = backfill_product_embeddings(
            session,
            provider=provider,
            batch_size=2,
            limit=10,
        )
        assert third.embeddings_written == 0
        assert len(provider.calls) == 2
    engine.dispose()


def test_changed_document_marks_embedding_stale_then_refreshes_it() -> None:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    provider = FakeEmbeddingProvider()
    with Session(engine) as session:
        product = _product("clus_a")
        session.add(product)
        session.commit()
        backfill_product_embeddings(session, provider=provider, batch_size=10, limit=10)

        product.ram_gb = 32
        repository = ProductRetrievalRepository(session)
        repository.upsert_document(build_canonical_product_document(product))
        session.commit()

        stale = get_embedding_index_health(
            session,
            provider=provider.provider_name,
            model_name=provider.model_name,
            dimensions=provider.dimensions,
        )
        assert stale.current_embeddings == 0
        assert stale.stale_embeddings == 1
        assert stale.ready is False

        refreshed = backfill_product_embeddings(
            session,
            provider=provider,
            batch_size=10,
            limit=10,
        )
        assert refreshed.embeddings_written == 1
        assert refreshed.health.current_embeddings == 1
        assert refreshed.health.stale_embeddings == 0
        assert refreshed.health.ready is True
    engine.dispose()
