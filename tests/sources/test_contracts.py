from datetime import UTC, datetime

import pytest
from pydantic import AnyHttpUrl, ValidationError

from sources import (
    FetchedSourceDocument,
    ParsedProduct,
    RawSourceRecord,
    SourceProductReference,
)


def test_raw_source_record_accepts_auditable_evidence() -> None:
    record = RawSourceRecord(
        source="example",
        source_product_id="SKU-1",
        category="laptop",
        subcategory="business_laptop",
        source_url="https://example.com/product",
        observed_at=datetime.now(UTC),
        payload={"title": "Laptop"},
        content_hash="0123456789abcdef",
    )

    assert record.source_product_id == "SKU-1"


def test_raw_source_record_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawSourceRecord(
            source="example",
            source_product_id="SKU-1",
            category="laptop",
            source_url="https://example.com/product",
            observed_at=datetime.now(),
            payload={},
            content_hash="0123456789abcdef",
        )


def test_raw_source_record_rejects_unregistered_category() -> None:
    with pytest.raises(ValidationError, match="unsupported category"):
        RawSourceRecord(
            source="example",
            source_product_id="SKU-1",
            category="television",
            source_url="https://example.com/product",
            observed_at=datetime.now(UTC),
            payload={},
            content_hash="0123456789abcdef",
        )


def test_source_product_reference_rejects_unregistered_category() -> None:
    with pytest.raises(ValidationError, match="unsupported category"):
        SourceProductReference(
            source_product_id="SKU-1",
            category="television",
            source_url="https://example.com/product",
        )


def test_source_product_reference_rejects_unknown_subcategory() -> None:
    with pytest.raises(ValidationError, match="unsupported laptop subcategory"):
        SourceProductReference(
            source_product_id="SKU-1",
            category="laptop",
            subcategory="desktop",
            source_url="https://example.com/product",
        )


def test_fetched_document_requires_timezone_aware_observation() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        FetchedSourceDocument(
            observed_at=datetime.now(),
            payload={},
            content_hash="0123456789abcdef",
        )


def test_parsed_product_contract_and_conversions() -> None:
    now = datetime.now(UTC)
    parsed = ParsedProduct(
        source="amazon",
        source_product_id="B0TEST123",
        category="laptop",
        subcategory="business_laptop",
        title="Lenovo ThinkBook 14",
        brand="Lenovo",
        model_name="ThinkBook 14 Gen 6",
        price_paise=5499000,
        mrp_paise=8250000,
        in_stock=True,
        seller="Appario Retail",
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B0TEST123"),
        attributes={"ram_gb": 16, "storage_gb": 512},
        observed_at=now,
    )

    fp = parsed.to_fingerprint()
    assert fp.category == "laptop"
    assert parsed.subcategory == "business_laptop"
    assert fp.brand == "lenovo"
    assert fp.ram_gb == 16
    assert "ram_gb" not in fp.attributes

    obs_default = parsed.to_price_observation()
    assert obs_default.offer_id == "amazon:B0TEST123"
    assert obs_default.price_paise == 5499000

    obs_custom = parsed.to_price_observation(offer_id="custom-offer-1")
    assert obs_custom.offer_id == "custom-offer-1"
    assert obs_custom.price_paise == 5499000


def test_parsed_product_rejects_naive_timestamp_and_bad_category() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ParsedProduct(
            source="amazon",
            source_product_id="B0TEST123",
            category="laptop",
            title="Laptop",
            brand="Brand",
            model_name="Model",
            price_paise=5000,
            source_url=AnyHttpUrl("https://www.amazon.in/dp/B0TEST123"),
            observed_at=datetime.now(),
        )

    with pytest.raises(ValidationError, match="unsupported category"):
        ParsedProduct(
            source="amazon",
            source_product_id="B0TEST123",
            category="unsupported_cat",
            title="Laptop",
            brand="Brand",
            model_name="Model",
            price_paise=5000,
            source_url=AnyHttpUrl("https://www.amazon.in/dp/B0TEST123"),
            observed_at=datetime.now(UTC),
        )


def test_parsed_product_rejects_unknown_subcategory() -> None:
    with pytest.raises(ValidationError, match="unsupported laptop subcategory"):
        ParsedProduct(
            source="amazon",
            source_product_id="B0TEST123",
            category="laptop",
            subcategory="desktop",
            title="Laptop",
            brand="Brand",
            model_name="Model",
            price_paise=5000,
            source_url=AnyHttpUrl("https://www.amazon.in/dp/B0TEST123"),
            observed_at=datetime.now(UTC),
        )
