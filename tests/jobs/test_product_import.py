"""Tests for supported product URL intake and durable worker orchestration."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import AnyHttpUrl

from jobs.product_import import (
    ProductImportRepository,
    ProductImportRequest,
    ProductImportStatus,
    build_product_search_queries,
    build_product_search_query,
    parse_product_url,
)
from sources.contracts import FetchedSourceDocument, ParsedProduct, SourceTransport
from storage.engine import create_database_engine, get_session_factory, init_db
from storage.models import ProductImportRunModel, ProductModel, RetailerProductModel
from workers.tasks import task_import_product_url


class ImportTransport(SourceTransport):
    def __init__(self, html: str) -> None:
        self.html = html
        self.calls = 0

    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        self.calls += 1
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={"html": self.html},
            content_hash="0123456789abcdef0123456789abcdef",
        )


class RoutedImportTransport(SourceTransport):
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        url = str(source_url)
        self.urls.append(url)
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={"html": self.responses.get(url, "<html></html>")},
            content_hash="0123456789abcdef0123456789abcdef",
        )


class ProgressRedis:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.events.append((channel, payload))


def test_parse_product_url_supports_only_exact_retailer_product_urls() -> None:
    amazon = parse_product_url(
        "https://www.amazon.in/gp/product/B0ABCDE123?tag=tracker",
        "laptop",
    )
    flipkart = parse_product_url(
        "https://www.flipkart.com/asus-phone/p/itmABC123?pid=MOB123",
        "mobile",
    )
    croma = parse_product_url(
        "https://www.croma.com/asus-laptop/p/324343?utm_source=test",
        "laptop",
    )

    assert str(amazon.source_url) == "https://www.amazon.in/dp/B0ABCDE123"
    assert amazon.source_product_id == "B0ABCDE123"
    assert flipkart.source_product_id == "itmABC123"
    assert "?" not in str(flipkart.source_url)
    assert croma.source_product_id == "324343"
    assert parse_product_url("https://www.amazon.in/dp/B0ABCDE123", "auto").category == "auto"

    with pytest.raises(ValueError, match="Unsupported retailer host"):
        parse_product_url("https://example.com/dp/B0ABCDE123", "laptop")
    with pytest.raises(ValueError, match="Could not identify"):
        parse_product_url("https://www.amazon.in/s?k=laptop", "laptop")
    with pytest.raises(ValueError, match="standard HTTPS"):
        parse_product_url("http://www.croma.com/product/p/324343", "laptop")


def test_build_product_search_query_prefers_retail_part_number_from_title() -> None:
    product = ParsedProduct(
        source="flipkart",
        source_product_id="itm6d89aef2f30f7",
        category="laptop",
        title="Apple MacBook Air M5 16 GB 1 TB MDVT4HN/A",
        brand="Apple",
        model_name="apple macbook air m5 16gb / 1024gb",
        price_paise=21_590_000,
        source_url=AnyHttpUrl("https://www.flipkart.com/apple/p/itm6d89aef2f30f7"),
        observed_at=datetime.now(UTC),
    )

    assert build_product_search_query(product) == "Apple MDVT4HN/A"
    assert build_product_search_queries(product) == (
        "Apple MDVT4HN/A",
        "Apple macbook air m5 16gb / 1024gb",
    )


@pytest.mark.asyncio
async def test_import_task_reuses_fresh_catalog_listing_without_fetch() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    with session_factory() as session, session.begin():
        session.add(
            ProductModel(
                id="clus_existing",
                category="laptop",
                brand="Apple",
                model_name="MacBook Air",
                attributes={},
            )
        )
        session.add(
            RetailerProductModel(
                canonical_product_id="clus_existing",
                source="amazon",
                source_product_id="B0ABCDE123",
                source_url="https://www.amazon.in/dp/B0ABCDE123",
                title="Apple MacBook Air",
                brand="Apple",
                model_name="MacBook Air",
                attributes={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.add(
            RetailerProductModel(
                canonical_product_id="clus_existing",
                source="croma",
                source_product_id="324343",
                source_url="https://www.croma.com/apple-macbook-air/p/324343",
                title="Apple MacBook Air",
                brand="Apple",
                model_name="MacBook Air",
                attributes={},
                last_seen_at=datetime.now(UTC),
            )
        )

    transport = ImportTransport("<html></html>")
    progress = ProgressRedis()
    run_id = str(uuid4())
    result = await task_import_product_url(
        {
            "db_engine": engine,
            "session_factory": session_factory,
            "transport": transport,
            "redis": progress,
        },
        {
            "run_id": run_id,
            "category": "laptop",
            "product_url": "https://www.amazon.in/dp/B0ABCDE123",
        },
    )

    assert result["status"] == "succeeded"
    assert result["canonical_product_id"] == "clus_existing"
    assert result["reused_fresh_listing"] is True
    assert transport.calls == 0
    assert progress.events[-1][0].endswith(run_id)
    with session_factory() as session:
        run = session.get(ProductImportRunModel, run_id)
        assert run is not None
        assert run.progress_stage == "completed"
    engine.dispose()


@pytest.mark.asyncio
async def test_import_task_collects_and_resolves_canonical_product() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    transport = ImportTransport(
        """
        <html>
            <span id="productTitle">Apple MacBook Air M5 16GB 512GB</span>
            <span class="a-price"><span class="a-offscreen">₹1,34,990</span></span>
        </html>
        """
    )
    run_id = str(uuid4())

    result = await task_import_product_url(
        {
            "db_engine": engine,
            "session_factory": session_factory,
            "transport": transport,
        },
        {
            "run_id": run_id,
            "category": "laptop",
            "product_url": "https://www.amazon.in/dp/B0TEST9999",
            "freshness_seconds": 0,
        },
    )

    assert result["status"] == "succeeded"
    assert str(result["canonical_product_id"]).startswith("clus_")
    # Seed fetch plus two exact-first search queries on each other retailer.
    assert transport.calls == 5
    with session_factory() as session:
        run = session.get(ProductImportRunModel, run_id)
        assert run is not None
        assert run.status == ProductImportStatus.SUCCEEDED.value
        assert run.canonical_product_id == result["canonical_product_id"]
    engine.dispose()


@pytest.mark.asyncio
async def test_import_task_discovers_and_links_matching_cross_source_offer() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    seed_url = "https://www.amazon.in/dp/B0M5AIR001"
    flipkart_search = "https://www.flipkart.com/search?q=Apple+MDVT4HN%2FA"
    flipkart_url = "https://www.flipkart.com/apple-macbook-air/p/itmM5AIR001"
    transport = RoutedImportTransport(
        {
            seed_url: """
                <span id="productTitle">Apple MacBook Air M5 16GB 1TB MDVT4HN/A</span>
                <span class="a-price"><span class="a-offscreen">₹2,15,900</span></span>
            """,
            flipkart_search: ('<a href="/apple-macbook-air/p/itmM5AIR001?pid=COM1">MacBook</a>'),
            flipkart_url: """
                <span class="B_NuCI">Apple MacBook Air M5 16GB 1TB MDVT4HN/A</span>
                <div class="_30jeq3">₹2,09,900</div>
            """,
        }
    )
    run_id = str(uuid4())

    result = await task_import_product_url(
        {
            "db_engine": engine,
            "session_factory": session_factory,
            "transport": transport,
        },
        {
            "run_id": run_id,
            "category": "laptop",
            "product_url": seed_url,
            "freshness_seconds": 0,
        },
    )

    assert result["status"] == "succeeded"
    with session_factory() as session:
        listings = session.query(RetailerProductModel).all()
        assert {listing.source for listing in listings} == {"amazon", "flipkart"}, transport.urls
        assert {listing.canonical_product_id for listing in listings} == {
            result["canonical_product_id"]
        }
    engine.dispose()


def test_import_repository_cancels_only_active_run() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    request = ProductImportRequest(
        run_id=str(uuid4()),
        category="mobile",
        product_url="https://www.croma.com/phone/p/324343",
    )
    target = parse_product_url(request.product_url, request.category)

    with session_factory() as session, session.begin():
        repository = ProductImportRepository(session)
        run = repository.create_or_get(request, target)
        assert repository.cancel(run.id) is True
        assert repository.cancel(run.id) is False

    engine.dispose()
