from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import AnyHttpUrl
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from config import Settings
from jobs.cli import app
from sources import FetchedSourceDocument, SourceProductReference
from storage.engine import create_database_engine
from storage.images import ImageUploadCandidate, StoredImage
from storage.models import Base, ProductModel, RetailerProductModel

runner = CliRunner()


def image_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        image_upload_enabled=True,
        image_s3_bucket="test-product-images",
    )


def stored_image(candidate: ImageUploadCandidate) -> StoredImage:
    return StoredImage(
        url=AnyHttpUrl("https://images.example.com/products/test.webp"),
        key="products/test.webp",
        sha256="a" * 64,
        width=800,
        height=600,
        source_url=candidate.image_urls[0],
        source=candidate.source,
        source_product_id=candidate.source_product_id,
        observed_at=candidate.observed_at,
    )


def seed_image_product(database_url: str, *, include_source_images: bool) -> None:
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add(
            ProductModel(
                id="clus_test",
                category="laptop",
                brand="Test",
                model_name="Test Laptop",
                attributes={},
            )
        )
        session.add(
            RetailerProductModel(
                canonical_product_id="clus_test",
                source="amazon",
                source_product_id="TEST123",
                source_url="https://www.amazon.in/dp/TEST123",
                title="Test Laptop",
                brand="Test",
                model_name="Test Laptop",
                attributes=(
                    {"source_image_urls": ["https://m.media-amazon.com/images/test.jpg"]}
                    if include_source_images
                    else {}
                ),
            )
        )
    engine.dispose()


def assert_product_image(database_url: str) -> None:
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        product = session.get(ProductModel, "clus_test")
        assert product is not None
        assert product.attributes["image_url"] == ("https://images.example.com/products/test.webp")
    engine.dispose()


def test_doctor_hides_database_url() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "configured" in result.stdout
    assert "postgresql" not in result.stdout
    assert "amazon" in result.stdout
    assert "smoke" in result.stdout


def test_pipeline_demo_runs() -> None:
    result = runner.invoke(app, ["pipeline-demo"])

    assert result.exit_code == 0
    assert '"decision": "match"' in result.stdout


def test_list_sources_reports_configured_retailers() -> None:
    result = runner.invoke(app, ["list-sources"])

    assert result.exit_code == 0
    assert '"amazon"' in result.stdout
    assert '"croma"' in result.stdout
    assert '"flipkart"' in result.stdout


def test_list_departments_reports_electronics() -> None:
    result = runner.invoke(app, ["list-departments"])

    assert result.exit_code == 0
    assert '"electronics"' in result.stdout


def test_list_categories_reports_electronics_laptop_taxonomy() -> None:
    result = runner.invoke(app, ["list-categories"])

    assert result.exit_code == 0
    assert '"department_code": "electronics"' in result.stdout
    assert '"gaming_laptop"' in result.stdout


def test_collection_plan_reports_bounded_product_and_review_volume() -> None:
    result = runner.invoke(
        app,
        [
            "collection-plan",
            "--source",
            "amazon",
            "--available-products",
            "1000",
            "--profile",
            "incremental",
        ],
    )

    assert result.exit_code == 0
    assert '"planned_products": 100' in result.stdout
    assert '"maximum_review_records": 2500' in result.stdout


def test_parse_demo_runs_for_all_sources() -> None:
    for source in ["flipkart", "croma", "amazon"]:
        result = runner.invoke(app, ["parse-demo", "--source", source])
        assert result.exit_code == 0
        assert f'"source": "{source}"' in result.stdout
        assert '"cpu_model"' in result.stdout
        assert '"price_paise": 5499000' in result.stdout


def test_reconcile_demo_runs() -> None:
    result = runner.invoke(app, ["reconcile-demo"])
    assert result.exit_code == 0
    assert '"total_collected": 4' in result.stdout
    assert '"total_clusters": 2' in result.stdout
    assert '"multi_source_clusters": 1' in result.stdout
    assert '"best_source": "croma"' in result.stdout


def test_live_collect_command_runs() -> None:
    result = runner.invoke(app, ["live-collect"])
    assert result.exit_code == 0
    assert "Initiating live collection" in result.stdout


def test_bulk_collect_laptops_command_runs() -> None:
    result = runner.invoke(app, ["bulk-collect-laptops", "--limit", "1"])
    assert result.exit_code == 0
    assert "Starting Multi-Source Bulk Collection" in result.stdout


def test_bulk_collect_all_command_runs() -> None:
    result = runner.invoke(
        app, ["bulk-collect", "--category", "all", "--sources", "all", "--limit", "1"]
    )
    assert result.exit_code == 0
    assert "Starting Multi-Source Bulk Collection" in result.stdout


def test_backfill_product_images_persists_uploaded_gallery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'backfill.db'}"
    seed_image_product(database_url, include_source_images=True)
    settings = image_settings(database_url)

    def fake_upload(
        candidates: Iterable[ImageUploadCandidate],
        _settings: Settings,
    ) -> dict[str, tuple[StoredImage, ...]]:
        items = list(candidates)
        return {candidate.canonical_product_id: (stored_image(candidate),) for candidate in items}

    monkeypatch.setattr("jobs.cli.get_settings", lambda: settings)
    monkeypatch.setattr("storage.images.upload_image_candidates", fake_upload)

    result = runner.invoke(app, ["backfill-product-images", "--limit", "10"])

    assert result.exit_code == 0
    assert '"products_uploaded": 1' in result.stdout
    assert_product_image(database_url)


def test_refresh_catalog_images_fetches_retailer_page_and_persists_gallery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'refresh.db'}"
    seed_image_product(database_url, include_source_images=False)
    settings = image_settings(database_url)

    class FakeAdapter:
        def __init__(
            self,
            references: Iterable[SourceProductReference],
            _transport: object,
        ) -> None:
            self.reference = next(iter(references))

        async def fetch(self, _source_product_id: str) -> FetchedSourceDocument:
            return FetchedSourceDocument(
                observed_at=datetime.now(UTC),
                payload={"html": "<html></html>"},
                content_hash="b" * 64,
            )

        def parse(self, document: FetchedSourceDocument) -> SimpleNamespace:
            return SimpleNamespace(
                image_urls=(AnyHttpUrl("https://m.media-amazon.com/images/test.jpg"),),
                observed_at=document.observed_at,
            )

    def fake_upload(
        candidates: Iterable[ImageUploadCandidate],
        _settings: Settings,
    ) -> dict[str, tuple[StoredImage, ...]]:
        items = list(candidates)
        return {candidate.canonical_product_id: (stored_image(candidate),) for candidate in items}

    monkeypatch.setattr("jobs.cli.get_settings", lambda: settings)
    monkeypatch.setattr("jobs.cli.HttpSourceTransport", object)
    monkeypatch.setattr("jobs.cli.get_source_adapter", lambda _source: FakeAdapter)
    monkeypatch.setattr("storage.images.upload_image_candidates", fake_upload)

    result = runner.invoke(
        app,
        ["refresh-catalog-images", "--limit", "10", "--concurrency", "2"],
    )

    assert result.exit_code == 0
    assert '"retailer_pages_with_images": 1' in result.stdout
    assert '"products_uploaded": 1' in result.stdout
    assert_product_image(database_url)
