from datetime import UTC, datetime

from pydantic import AnyHttpUrl

from matching.reconciliation import reconcile_products
from sources.contracts import ParsedProduct


def sample_product(**updates: object) -> ParsedProduct:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "source": "flipkart",
        "source_product_id": "FLIP-001",
        "category": "laptop",
        "title": "Lenovo ThinkBook 14 Gen 6 Laptop (16GB, 512GB SSD)",
        "brand": "Lenovo",
        "model_name": "ThinkBook 14 Gen 6",
        "manufacturer_part_number": None,
        "gtin": None,
        "price_paise": 5499000,
        "mrp_paise": 8250000,
        "coupon_price_paise": None,
        "in_stock": True if "in_stock" not in updates else bool(updates["in_stock"]),
        "seller": "RetailNet",
        "rating": 4.4,
        "review_count": 18,
        "source_url": AnyHttpUrl("https://www.flipkart.com/p/itm123"),
        "attributes": {
            "cpu_model": "Ryzen 7 7730U",
            "gpu_model": "Integrated Radeon",
            "ram_gb": 16,
            "storage_gb": 512,
        },
        "observed_at": now,
    }
    values.update(updates)
    return ParsedProduct.model_validate(values)


def test_empty_reconciliation() -> None:
    report = reconcile_products([])
    assert report.total_collected == 0
    assert report.total_clusters == 0
    assert report.multi_source_clusters == 0
    assert report.clusters == ()


def test_reconcile_links_same_variant_across_multiple_sources() -> None:
    p_flipkart = sample_product(
        source="flipkart",
        source_product_id="FLIP-001",
        price_paise=5499000,
        source_url=AnyHttpUrl("https://www.flipkart.com/p/itm123"),
    )
    p_croma = sample_product(
        source="croma",
        source_product_id="CROMA-001",
        price_paise=5349000,
        source_url=AnyHttpUrl("https://www.croma.com/p/267890"),
    )
    p_amazon = sample_product(
        source="amazon",
        source_product_id="B0CX001",
        price_paise=5420000,
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B0CX001"),
    )

    report = reconcile_products([p_flipkart, p_croma, p_amazon])

    assert report.total_collected == 3
    assert report.total_clusters == 1
    assert report.multi_source_clusters == 1
    assert report.review_required_count == 0

    cluster = report.clusters[0]
    assert len(cluster.offers) == 3
    assert cluster.best_price_paise == 5349000
    assert cluster.best_source == "croma"
    assert cluster.max_price_paise == 5499000
    assert cluster.price_spread_paise == 150000
    assert cluster.savings_paise == 150000


def test_reconcile_keeps_different_configurations_separate() -> None:
    p16gb = sample_product(
        source="amazon",
        source_product_id="B016GB",
        price_paise=5400000,
        attributes={
            "cpu_model": "Ryzen 7 7730U",
            "gpu_model": "Integrated Radeon",
            "ram_gb": 16,
            "storage_gb": 512,
        },
    )
    p8gb = sample_product(
        source="flipkart",
        source_product_id="FLIP8GB",
        price_paise=4800000,
        attributes={
            "cpu_model": "Ryzen 7 7730U",
            "gpu_model": "Integrated Radeon",
            "ram_gb": 8,
            "storage_gb": 512,
        },
    )

    report = reconcile_products([p16gb, p8gb])

    assert report.total_collected == 2
    assert report.total_clusters == 2
    assert report.multi_source_clusters == 0


def test_reconcile_calculates_prices_with_out_of_stock() -> None:
    p_cheap_oos = sample_product(
        source="croma",
        source_product_id="CROMA-OOS",
        price_paise=4999000,
        in_stock=False,
    )
    p_instock = sample_product(
        source="amazon",
        source_product_id="AMZ-IN",
        price_paise=5500000,
        in_stock=True,
    )

    report = reconcile_products([p_cheap_oos, p_instock])

    cluster = report.clusters[0]
    assert cluster.best_price_paise == 5500000
    assert cluster.best_source == "amazon"


def test_reconcile_handles_review_matches() -> None:
    p_exact = sample_product(
        source="flipkart",
        source_product_id="FLIP-001",
        price_paise=5499000,
    )
    # Different ambiguous title that gets MatchDecision.REVIEW
    p_review = sample_product(
        source="amazon",
        source_product_id="B0UNCERTAIN",
        model_name="ThinkBook Business Edition",
        price_paise=5420000,
    )

    report = reconcile_products([p_exact, p_review])

    assert report.total_collected == 2
    assert report.total_clusters == 1
    assert report.review_required_count == 1
    assert len(report.clusters[0].review_candidates) == 1


def test_reconcile_all_offers_out_of_stock() -> None:
    p1 = sample_product(
        source="flipkart",
        source_product_id="FLIP-OOS1",
        price_paise=6000000,
        in_stock=False,
    )
    p2 = sample_product(
        source="amazon",
        source_product_id="AMZ-OOS2",
        price_paise=5800000,
        in_stock=False,
    )

    report = reconcile_products([p1, p2])

    cluster = report.clusters[0]
    assert cluster.best_price_paise == 5800000
    assert cluster.best_source == "amazon"
    assert cluster.price_spread_paise == 200000
    assert cluster.savings_paise == 200000


def test_cluster_identity_ignores_presentation_only_spec_changes() -> None:
    base_attributes = {
        "cpu_model": "Ryzen 7 7730U",
        "gpu_model": "Integrated Radeon",
        "ram_gb": 16,
        "storage_gb": 512,
    }
    first = sample_product(
        attributes={
            **base_attributes,
            "warranty": "1 year",
            "spec_sections": {"Memory": {"RAM": "16 GB"}},
        }
    )
    second = sample_product(
        attributes={
            **base_attributes,
            "warranty": "2 years",
            "spec_sections": {"Hardware": {"Installed Memory": "16 GB"}},
        }
    )

    first_cluster = reconcile_products([first]).clusters[0]
    second_cluster = reconcile_products([second]).clusters[0]

    assert first_cluster.cluster_id == second_cluster.cluster_id
    assert "spec_sections" not in first_cluster.fingerprint.attributes
    assert "cpu_model" not in first_cluster.fingerprint.attributes
    assert "ram_gb" not in first_cluster.fingerprint.attributes
    assert first_cluster.fingerprint.ram_gb == 16
    assert first_cluster.attributes["spec_sections"] == {"Memory": {"RAM": "16 GB"}}


def test_exact_matches_merge_complementary_spec_sections() -> None:
    base_attributes = {
        "cpu_model": "Core 5 210H",
        "ram_gb": 24,
        "storage_gb": 512,
    }
    first = sample_product(
        source="flipkart",
        source_product_id="FLIP-SPECS",
        attributes={
            **base_attributes,
            "spec_sections": {"Memory": {"RAM": "24 GB"}},
        },
    )
    second = sample_product(
        source="amazon",
        source_product_id="AMZ-SPECS",
        attributes={
            **base_attributes,
            "spec_sections": {
                "Display": {"Refresh rate": "144 Hz"},
                "Memory": {"RAM type": "DDR5"},
            },
        },
    )

    cluster = reconcile_products([first, second]).clusters[0]

    assert cluster.attributes["spec_sections"] == {
        "Memory": {"RAM": "24 GB", "RAM type": "DDR5"},
        "Display": {"Refresh rate": "144 Hz"},
    }
