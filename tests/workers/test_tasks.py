"""Tests for ARQ worker distributed tasks."""

from datetime import UTC, datetime

import pytest
from pydantic import AnyHttpUrl

from sources.contracts import FetchedSourceDocument, ParsedProduct, SourceTransport
from storage.engine import create_database_engine, get_session_factory, init_db
from workers.tasks import (
    infer_import_category,
    task_crawl_product,
    task_discover_sitemap,
    task_reconcile_and_persist,
    task_send_to_dead_letter,
)


class MockWorkerTransport(SourceTransport):
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload=self._payload,
            content_hash="0123456789abcdef0123456789abcdef",
        )


@pytest.mark.asyncio
async def test_task_discover_sitemap() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset>
        <url><loc>https://www.croma.com/apple-macbook-air/p/324343</loc></url>
        <url><loc>https://www.croma.com/dell-laptop/p/323906</loc></url>
    </urlset>
    """
    ctx = {"transport": MockWorkerTransport({"content": xml})}
    res = await task_discover_sitemap(
        ctx,
        source="croma",
        sitemap_url="https://croma.com/sitemap.xml",
        limit=5,
    )
    assert res["source"] == "croma"
    assert res["discovered_count"] == 2
    assert "324343" in res["product_ids"]


@pytest.mark.asyncio
async def test_task_crawl_product() -> None:
    html = """
    <html>
        <span id="productTitle">Apple MacBook Air M5 16GB 512GB</span>
        <span class="a-price"><span class="a-offscreen">₹1,34,990</span></span>
    </html>
    """
    ctx = {"transport": MockWorkerTransport({"html": html})}
    res = await task_crawl_product(
        ctx,
        url="https://www.amazon.in/dp/B0TEST999",
        source="amazon",
        source_product_id="B0TEST999",
    )
    assert res["status"] == "success"
    assert res["source"] == "amazon"
    assert res["brand"] == "Apple"
    assert res["price_paise"] == 13499000
    assert "parsed_payload" in res


def test_infer_import_category_from_product_identity() -> None:
    base = {
        "source": "amazon",
        "source_product_id": "B0TEST999",
        "source_url": "https://amazon.in/dp/B0TEST999",
        "category": "laptop",
        "brand": "ASUS",
        "model_name": "CX15",
        "price_paise": 2499000,
        "in_stock": True,
        "attributes": {},
        "observed_at": datetime.now(UTC),
    }
    laptop = ParsedProduct.model_validate({**base, "title": "ASUS Chromebook CX15 Laptop"})
    mobile = ParsedProduct.model_validate(
        {
            **base,
            "title": "OnePlus 13R 5G Smartphone",
            "brand": "OnePlus",
            "model_name": "13R",
        }
    )

    assert infer_import_category(laptop) == "laptop"
    assert infer_import_category(mobile) == "mobile"


@pytest.mark.asyncio
async def test_task_reconcile_and_persist() -> None:
    engine = create_database_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    ctx = {"db_engine": engine, "session_factory": session_factory}

    parsed1 = {
        "source": "amazon",
        "source_product_id": "B0TEST001",
        "source_url": "https://amazon.in/dp/B0TEST001",
        "category": "laptop",
        "title": "Apple MacBook Air M5 16GB 512GB",
        "brand": "Apple",
        "model_name": "MacBook Air M5",
        "price_paise": 13490000,
        "mrp_paise": 14990000,
        "in_stock": True,
        "attributes": {"ram_gb": 16, "storage_gb": 512},
        "observed_at": datetime.now(UTC).isoformat(),
    }

    parsed2 = {
        "source": "croma",
        "source_product_id": "324343",
        "source_url": "https://croma.com/p/324343",
        "category": "laptop",
        "title": "Apple MacBook Air M5 16GB 512GB",
        "brand": "Apple",
        "model_name": "MacBook Air M5",
        "price_paise": 13949000,
        "mrp_paise": 14990000,
        "in_stock": True,
        "attributes": {"ram_gb": 16, "storage_gb": 512},
        "observed_at": datetime.now(UTC).isoformat(),
    }

    res = await task_reconcile_and_persist(ctx, [parsed1, parsed2], persist=True)
    assert res["total_collected"] == 2
    assert res["total_clusters"] == 1
    assert res["multi_source_clusters"] == 1
    assert res["db_metrics"]["products_persisted"] == 1
    assert res["db_metrics"]["offers_persisted"] == 2


@pytest.mark.asyncio
async def test_task_send_to_dead_letter() -> None:
    res = await task_send_to_dead_letter(
        {},
        url="https://croma.com/p/999999",
        source="croma",
        error_message="HTTP 404 Not Found",
        attempts=3,
    )
    assert res["status"] == "dead_lettered"
    assert res["attempts"] == 3
    assert "dead_letter_queue" in res["queue"]
