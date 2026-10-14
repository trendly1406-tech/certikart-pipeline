from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from matching.reconciliation import reconcile_products
from sources.contracts import ParsedProduct
from storage.models import (
    Base,
    OfferModel,
    ProductModel,
    RetailerProductModel,
)
from storage.repository import PipelineRepository


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Create an isolated in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()


def sample_parsed_product(
    source: str,
    source_product_id: str,
    title: str,
    price_paise: int = 5000000,
    mrp_paise: int = 6000000,
    in_stock: bool = True,
    observed_at: datetime | None = None,
) -> ParsedProduct:
    return ParsedProduct(
        source=source,
        source_product_id=source_product_id,
        category="laptop",
        source_url=f"https://www.{source}.com/p/{source_product_id}",
        title=title,
        brand="Apple",
        model_name="MacBook Air M5",
        price_paise=price_paise,
        mrp_paise=mrp_paise,
        coupon_price_paise=None,
        in_stock=in_stock,
        seller="Authorized Retailer",
        rating=4.8,
        review_count=120,
        observed_at=observed_at or datetime.now(UTC),
        attributes={
            "ram_gb": 16,
            "storage_gb": 512,
            "cpu_model": "apple m5",
            "gpu_model": "integrated graphics",
        },
    )


def test_idempotent_report_persistence(db_session: Session) -> None:
    t0 = datetime.now(UTC)
    p1 = sample_parsed_product("amazon", "B01", "Apple MacBook Air M5 16/512", observed_at=t0)
    report1 = reconcile_products([p1])

    res1 = PipelineRepository.persist_reconciliation_report(db_session, report1)
    db_session.commit()

    assert res1["products_persisted"] == 1
    assert res1["offers_persisted"] == 1

    # Verify counts in DB
    assert (
        db_session.scalar(
            select(ProductModel).where(ProductModel.id == report1.clusters[0].cluster_id)
        )
        is not None
    )
    assert (
        db_session.scalar(
            select(RetailerProductModel).where(RetailerProductModel.source_product_id == "B01")
        )
        is not None
    )
    assert len(db_session.scalars(select(OfferModel)).all()) == 1

    # Re-run identical scrape 10 minutes later (Same price)
    t1 = t0 + timedelta(minutes=10)
    p2 = sample_parsed_product("amazon", "B01", "Apple MacBook Air M5 16/512", observed_at=t1)
    report2 = reconcile_products([p2])

    res2 = PipelineRepository.persist_reconciliation_report(db_session, report2)
    db_session.commit()

    # Should update last_seen_at without duplicating records
    assert res2["products_persisted"] == 0
    assert len(db_session.scalars(select(ProductModel)).all()) == 1
    assert len(db_session.scalars(select(RetailerProductModel)).all()) == 1
    assert len(db_session.scalars(select(OfferModel)).all()) == 1

    # Verify last_seen_at was updated
    ret_prod = db_session.scalar(
        select(RetailerProductModel).where(RetailerProductModel.source_product_id == "B01")
    )
    assert ret_prod is not None
    assert ret_prod.last_seen_at.replace(tzinfo=UTC) == t1


def test_multi_retailer_canonical_linking(db_session: Session) -> None:
    p_amz = sample_parsed_product(
        "amazon",
        "B01_MAC",
        "Apple 2026 MacBook Air 15″ Laptop with M5 chip, 16GB RAM, 512GB SSD",
        price_paise=13490000,
    )
    p_flip = sample_parsed_product(
        "flipkart",
        "FLIP_MAC",
        "Apple Macbook Air (M5, 2026) M5 - (16 GB/512 GB SSD/macOS) 15-inch",
        price_paise=12990000,
    )

    report = reconcile_products([p_amz, p_flip])
    assert report.total_clusters == 1
    assert len(report.clusters[0].offers) == 2

    res = PipelineRepository.persist_reconciliation_report(db_session, report)
    db_session.commit()

    assert res["products_persisted"] == 1
    assert res["offers_persisted"] == 2

    # Verify single canonical product linked to both retailer products
    canonical_prod = PipelineRepository.get_canonical_product(
        db_session, report.clusters[0].cluster_id
    )
    assert canonical_prod is not None
    assert len(canonical_prod.retailer_products) == 2
    assert {rp.source for rp in canonical_prod.retailer_products} == {"amazon", "flipkart"}
    assert len(canonical_prod.offers) == 2


def test_price_change_updates_offer(db_session: Session) -> None:
    t0 = datetime.now(UTC)
    p1 = sample_parsed_product(
        "amazon", "B01", "Apple MacBook Air M5 16/512", price_paise=13490000, observed_at=t0
    )
    report1 = reconcile_products([p1])
    PipelineRepository.persist_reconciliation_report(db_session, report1)
    db_session.commit()

    offer1 = db_session.scalar(select(OfferModel).where(OfferModel.source_product_id == "B01"))
    assert offer1 is not None
    assert offer1.price_paise == 13490000

    # Price drops by ₹5,000 next day
    t1 = t0 + timedelta(days=1)
    p2 = sample_parsed_product(
        "amazon", "B01", "Apple MacBook Air M5 16/512", price_paise=12990000, observed_at=t1
    )
    report2 = reconcile_products([p2])
    PipelineRepository.persist_reconciliation_report(db_session, report2)
    db_session.commit()

    offer2 = db_session.scalar(select(OfferModel).where(OfferModel.source_product_id == "B01"))
    assert offer2 is not None
    assert offer2.price_paise == 12990000


def test_existing_listing_keeps_canonical_id_when_derived_identity_changes(
    db_session: Session,
) -> None:
    original = sample_parsed_product("amazon", "B01", "Apple MacBook Air M5 16/512")
    original_report = reconcile_products([original])
    original_id = original_report.clusters[0].cluster_id
    PipelineRepository.persist_reconciliation_report(db_session, original_report)
    db_session.commit()

    changed = original.model_copy(
        update={
            "model_name": "MacBook Air M5 2026",
            "attributes": {
                **original.attributes,
                "storage_gb": 1024,
                "spec_sections": {"Storage": {"Capacity": "1 TB"}},
            },
        }
    )
    changed_report = reconcile_products([changed])
    assert changed_report.clusters[0].cluster_id != original_id

    result = PipelineRepository.persist_reconciliation_report(db_session, changed_report)
    db_session.commit()

    products = db_session.scalars(select(ProductModel)).all()
    retailer = db_session.scalar(
        select(RetailerProductModel).where(RetailerProductModel.source_product_id == "B01")
    )
    offer = db_session.scalar(select(OfferModel).where(OfferModel.source_product_id == "B01"))
    assert result["products_persisted"] == 0
    assert [product.id for product in products] == [original_id]
    assert retailer is not None
    assert retailer.canonical_product_id == original_id
    assert offer is not None
    assert offer.canonical_product_id == original_id
    assert products[0].model_name == "macbook air m5 2026"


def test_sparse_refresh_preserves_existing_rich_spec_sections(db_session: Session) -> None:
    base = sample_parsed_product("flipkart", "FLIP-RICH", "Apple MacBook Air M5 16/512")
    rich = base.model_copy(
        update={
            "attributes": {
                **base.attributes,
                "spec_sections": {
                    "Display": {"Refresh rate": "60 Hz", "Panel": "IPS"},
                    "Memory": {"RAM type": "LPDDR5X"},
                },
            }
        }
    )
    report = reconcile_products([rich])
    product_id = report.clusters[0].cluster_id
    PipelineRepository.persist_reconciliation_report(db_session, report)
    db_session.commit()

    sparse = base.model_copy(update={"observed_at": datetime.now(UTC) + timedelta(hours=1)})
    PipelineRepository.persist_reconciliation_report(db_session, reconcile_products([sparse]))
    db_session.commit()

    stored = db_session.get(ProductModel, product_id)
    assert stored is not None
    assert stored.attributes["spec_sections"] == {
        "Display": {"Refresh rate": "60 Hz", "Panel": "IPS"},
        "Memory": {"RAM type": "LPDDR5X"},
    }
