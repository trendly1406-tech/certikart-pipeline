from datetime import UTC, datetime

import pytest
from pydantic import AnyHttpUrl

from collectors.discovery import discover_laptop_references, discover_product_references
from sources.contracts import FetchedSourceDocument, SourceTransport


class MockDiscoveryTransport(SourceTransport):
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument:
        url_str = str(source_url)
        html = self._responses.get(url_str, "<html></html>")
        return FetchedSourceDocument(
            observed_at=datetime.now(UTC),
            payload={"html": html},
            content_hash="0123456789abcdef0123456789abcdef",
        )


@pytest.mark.asyncio
async def test_discover_flipkart_laptops() -> None:
    html = """
    <html>
        <a href="/lenovo-thinkbook-14/p/itm123456?pid=1">Link 1</a>
        <a href="/hp-pavilion-15/p/itm789012?pid=2">Link 2</a>
    </html>
    """
    transport = MockDiscoveryTransport(
        {
            "https://www.flipkart.com/search?q=laptop&page=1": html,
        }
    )
    refs = await discover_laptop_references("flipkart", transport, max_items=2)
    assert len(refs) == 2
    assert refs[0].source_product_id == "itm123456"
    assert refs[1].source_product_id == "itm789012"
    assert refs[0].category == "laptop"


@pytest.mark.asyncio
async def test_discover_amazon_laptops() -> None:
    html = """
    <html>
        <div data-asin="B0CX000001"></div>
        <div data-asin="B0CX000002"></div>
    </html>
    """
    transport = MockDiscoveryTransport(
        {
            "https://www.amazon.in/s?k=laptop&page=1": html,
        }
    )
    refs = await discover_laptop_references("amazon", transport, max_items=2)
    assert len(refs) == 2
    assert refs[0].source_product_id == "B0CX000001"
    assert refs[1].source_product_id == "B0CX000002"


@pytest.mark.asyncio
async def test_discover_croma_laptops() -> None:
    html = """
    <html>
        <a href="/p/316655">Product 1</a>
        <a href="/p/322265">Product 2</a>
    </html>
    """
    transport = MockDiscoveryTransport(
        {
            "https://www.croma.com/computers-tablets/laptops/c/20?page=0": html,
        }
    )
    refs = await discover_laptop_references("croma", transport, max_items=2)
    assert len(refs) == 2
    assert refs[0].source_product_id == "316655"
    assert refs[1].source_product_id == "322265"


@pytest.mark.asyncio
async def test_discover_croma_laptops_ssr_plp() -> None:
    html = """
    <html>
        <script>
        window.__INITIAL_DATA__= {
            "plpReducer": {
                "plpData": {
                    "products": [
                        {
                            "code": "324343",
                            "url": "/apple-macbook-air-m5/p/324343",
                            "name": "Apple MacBook Air M5",
                        },
                        {
                            "code": "323906",
                            "url": "/dell-inspiron-15/p/323906",
                            "name": "Dell Inspiron 15",
                        },
                    ]
                }
            },
            "errorMessage": undefined
        };
        </script>
    </html>
    """
    transport = MockDiscoveryTransport(
        {
            "https://www.croma.com/computers-tablets/laptops/c/20?page=0": html,
        }
    )
    refs = await discover_laptop_references("croma", transport, max_items=2)
    assert len(refs) == 2
    assert refs[0].source_product_id == "324343"
    assert str(refs[0].source_url) == "https://www.croma.com/apple-macbook-air-m5/p/324343"
    assert refs[1].source_product_id == "323906"
    assert str(refs[1].source_url) == "https://www.croma.com/dell-inspiron-15/p/323906"


@pytest.mark.asyncio
async def test_discover_unsupported_source_raises() -> None:
    transport = MockDiscoveryTransport({})
    with pytest.raises(ValueError, match="unsupported discovery source"):
        await discover_laptop_references("unknown_source", transport)


@pytest.mark.asyncio
async def test_discover_zero_items_returns_empty() -> None:
    transport = MockDiscoveryTransport({})
    refs = await discover_laptop_references("flipkart", transport, max_items=0)
    assert refs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "search_url", "html", "expected_id"),
    [
        (
            "amazon",
            "https://www.amazon.in/s?k=Apple+MDVT4HN%2FA",
            '<div data-asin="B0M5AIR001"><h2>Apple MacBook Air M5</h2></div>',
            "B0M5AIR001",
        ),
        (
            "flipkart",
            "https://www.flipkart.com/search?q=Apple+MDVT4HN%2FA",
            '<a href="/apple-macbook-air/p/itmM5AIR001?pid=COM1">MacBook</a>',
            "itmM5AIR001",
        ),
        (
            "croma",
            "https://www.croma.com/searchB?q=Apple+MDVT4HN%2FA",
            """
            <script>window.__INITIAL_DATA__={"plpReducer":{"plpData":{"products":[
              {"code":"345678","url":"/apple-macbook-air-m5/p/345678","name":"Apple MacBook Air M5"}
            ]}}};</script>
            """,
            "345678",
        ),
    ],
)
async def test_discover_exact_product_references(
    source: str,
    search_url: str,
    html: str,
    expected_id: str,
) -> None:
    transport = MockDiscoveryTransport({search_url: html})

    refs = await discover_product_references(
        source,
        "Apple MDVT4HN/A",
        "laptop",
        transport,
        max_items=2,
    )

    assert [reference.source_product_id for reference in refs] == [expected_id]


@pytest.mark.asyncio
async def test_exact_product_discovery_has_no_unrelated_seed_fallback() -> None:
    transport = MockDiscoveryTransport({})

    refs = await discover_product_references(
        "amazon",
        "Apple MDVT4HN/A",
        "laptop",
        transport,
    )

    assert refs == []


@pytest.mark.asyncio
async def test_exact_amazon_discovery_prioritizes_matching_brand() -> None:
    html = """
    <div data-asin="B0OTHER001"><h2>Unrelated Watch</h2></div>
    <div data-asin="B0OTHER002"><h2>Generic Laptop</h2></div>
    <div data-asin="B0APPLE001"><h2>Apple MacBook Air M5 16GB 1TB</h2></div>
    """
    transport = MockDiscoveryTransport({"https://www.amazon.in/s?k=Apple+MDVT4HN%2FA": html})

    refs = await discover_product_references(
        "amazon",
        "Apple MDVT4HN/A",
        "laptop",
        transport,
        max_items=1,
    )

    assert [reference.source_product_id for reference in refs] == ["B0APPLE001"]


@pytest.mark.asyncio
async def test_discover_amazon_seed_fallback() -> None:
    transport = MockDiscoveryTransport({})
    refs = await discover_laptop_references("amazon", transport, max_items=5)
    assert len(refs) == 5
    assert all(r.category == "laptop" for r in refs)


@pytest.mark.asyncio
async def test_discover_croma_seed_fallback() -> None:
    transport = MockDiscoveryTransport({})
    refs = await discover_laptop_references("croma", transport, max_items=5)
    assert len(refs) == 5
    assert all(r.category == "laptop" for r in refs)
