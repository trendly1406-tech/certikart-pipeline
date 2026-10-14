from datetime import UTC, datetime

import pytest
from pydantic import AnyHttpUrl

from sources.amazon.parser import parse_amazon_payload
from sources.croma.parser import parse_croma_payload
from sources.flipkart.parser import parse_flipkart_payload
from sources.images import image_urls


@pytest.mark.parametrize("source", ["amazon", "flipkart", "croma"])
def test_parsers_collect_product_images(source: str) -> None:
    parsers = {
        "amazon": parse_amazon_payload,
        "flipkart": parse_flipkart_payload,
        "croma": parse_croma_payload,
    }
    payload: dict[str, object] = {
        "html": """<html><script type="application/ld+json">{
      "@type":"Product", "name":"ASUS Chromebook CX15 4GB 64GB",
      "image":["https://media.croma.com/product.jpg", "javascript:bad"],
      "offers":{"price":"24990"}
    }</script><meta property="og:image" content="https://media.croma.com/product.jpg"></html>"""
    }
    parsed = parsers[source](
        payload=payload,
        source_url=AnyHttpUrl(f"https://www.{source}.com/product"),
        source_product_id="TEST",
        observed_at=datetime.now(UTC),
        category="laptop",
    )
    assert parsed.image_urls == (AnyHttpUrl("https://media.croma.com/product.jpg"),)


def test_invalid_and_duplicate_image_urls_are_filtered() -> None:
    assert image_urls(
        [
            None,
            "javascript:alert(1)",
            "https://u:p@cdn.example.com/x",
            ["https://cdn.example.com/a", "https://cdn.example.com/a"],
        ],
        AnyHttpUrl("https://www.example.com"),
    ) == (AnyHttpUrl("https://cdn.example.com/a"),)


def test_amazon_resize_variants_of_same_asset_are_deduplicated() -> None:
    parsed = parse_amazon_payload(
        payload={
            "image_urls": [
                "https://m.media-amazon.com/images/I/asset._AC_SX679_.jpg",
                "https://m.media-amazon.com/images/I/asset._AC_UY218_.jpg",
                "https://m.media-amazon.com/images/I/another-photo._AC_SX679_.jpg",
            ],
            "html": '<meta property="og:image" content="https://m.media-amazon.com/images/I/asset.jpg">',
        },
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B01"),
        source_product_id="B01",
        observed_at=datetime.now(UTC),
        category="laptop",
    )

    assert parsed.image_urls == (
        AnyHttpUrl("https://m.media-amazon.com/images/I/asset._AC_SX679_.jpg"),
        AnyHttpUrl("https://m.media-amazon.com/images/I/another-photo._AC_SX679_.jpg"),
    )
