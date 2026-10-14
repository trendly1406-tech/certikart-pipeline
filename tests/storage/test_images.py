from datetime import UTC, datetime
from io import BytesIO

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from PIL import Image
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from config import Settings
from matching.reconciliation import reconcile_products
from sources.contracts import ParsedProduct
from storage.images import (
    S3ImageStore,
    SupabaseImageStore,
    download_image,
    optimize_image,
    upload_report_images,
)
from storage.models import Base, OfferModel, ProductModel
from storage.repository import PipelineRepository


def settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "image_bucket": "",
            "image_supabase_url": "",
            "image_supabase_secret_key": "",
            "image_s3_bucket": "test-product-images",
            "image_s3_region": "ap-south-1",
            "image_public_base_url": "https://images.example.com",
            **overrides,
        }
    )


def image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1600, 900), "white").save(output, format="JPEG")
    return output.getvalue()


def product() -> ParsedProduct:
    return ParsedProduct(
        source="amazon",
        source_product_id="B01",
        category="laptop",
        source_url=AnyHttpUrl("https://www.amazon.in/dp/B01"),
        title="ASUS Chromebook CX15 Intel N50 (4 GB/64 GB eMMC/Chrome OS)",
        brand="ASUS",
        model_name="CX15",
        price_paise=2499000,
        attributes={"ram_gb": 4, "storage_gb": 64, "cpu_model": "intel n50"},
        image_urls=(AnyHttpUrl("https://m.media-amazon.com/images/example.jpg"),),
        observed_at=datetime.now(UTC),
    )


def test_images_do_not_change_product_identity() -> None:
    original = product()
    without_images = original.model_copy(update={"image_urls": ()})
    assert reconcile_products([original]).clusters[0].cluster_id == (
        reconcile_products([without_images]).clusters[0].cluster_id
    )


def test_disabled_uploads_do_not_contact_s3() -> None:
    assert upload_report_images(reconcile_products([product()]), settings()) == {}


def test_storage_configuration_requires_bucket_and_valid_optional_base_url() -> None:
    with pytest.raises(ValidationError, match="bucket"):
        settings(image_upload_enabled=True, image_s3_bucket="")
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(image_public_base_url="http://images.example.com")
    with pytest.raises(ValidationError, match="endpoint URL must be HTTPS"):
        settings(image_s3_endpoint_url="http://storage.example.com")
    assert settings(image_public_base_url="").image_public_base_url is None
    assert settings(image_s3_endpoint_url="").image_s3_endpoint_url is None

    with pytest.raises(ValidationError, match="server-side secret key"):
        settings(
            image_upload_enabled=True,
            image_bucket="product-images",
            image_supabase_url="https://project.supabase.co",
        )


@pytest.mark.parametrize(
    ("bucket", "region", "expected"),
    [
        (
            "test-product-images",
            "ap-south-1",
            "https://test-product-images.s3.ap-south-1.amazonaws.com/",
        ),
        ("product.images", "ap-south-1", "https://s3.ap-south-1.amazonaws.com/product.images"),
        (
            "test-product-images",
            "cn-north-1",
            "https://test-product-images.s3.cn-north-1.amazonaws.com.cn/",
        ),
    ],
)
def test_direct_s3_base_without_cdn(bucket: str, region: str, expected: str) -> None:
    configured = settings(
        image_upload_enabled=True,
        image_public_base_url="",
        image_s3_bucket=bucket,
        image_s3_region=region,
    )
    assert str(configured.image_delivery_base_url) == expected


def test_upload_returns_direct_s3_url_without_custom_base() -> None:
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stubber:
        stubber.add_response("put_object", {})
        uploaded = S3ImageStore(settings(image_public_base_url=""), client).put(image_bytes())
        assert str(uploaded[0]) == (
            f"https://test-product-images.s3.ap-south-1.amazonaws.com/{uploaded[1]}"
        )
        stubber.assert_no_pending_responses()


def test_supabase_s3_client_uses_custom_endpoint_and_path_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = object()

    def build_client(service: str, **kwargs: object) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return client

    monkeypatch.setattr("storage.images.boto3.client", build_client)
    configured = settings(
        image_s3_endpoint_url="https://project.storage.supabase.co/storage/v1/s3",
        image_s3_force_path_style=True,
        image_s3_access_key_id="test-access",
        image_s3_secret_access_key="test-secret",
    )

    store = S3ImageStore(configured)

    assert store.client is client
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://project.storage.supabase.co/storage/v1/s3"
    assert captured["region_name"] == "ap-south-1"
    assert captured["aws_access_key_id"] == "test-access"
    assert getattr(captured["config"], "s3", None) == {"addressing_style": "path"}


def test_supabase_store_uploads_and_returns_public_url() -> None:
    def upload(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/storage/v1/object/product-images/products/webp-v1/")
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.headers["apikey"] == "test-secret"
        assert request.headers["x-upsert"] == "true"
        assert request.headers["content-type"] == "image/webp"
        return httpx.Response(200, json={"Key": "stored"})

    configured = settings(
        image_bucket="product-images",
        image_s3_bucket="",
        image_supabase_url="https://project.supabase.co",
        image_supabase_secret_key="test-secret",
        image_public_base_url="",
    )
    with httpx.Client(transport=httpx.MockTransport(upload)) as client:
        uploaded = SupabaseImageStore(configured, client).put(image_bytes())

    assert str(uploaded[0]).startswith(
        "https://project.supabase.co/storage/v1/object/public/product-images/products/webp-v1/"
    )


def test_webp_is_resized_and_has_no_exif() -> None:
    content, width, height = optimize_image(image_bytes(), 1200)
    assert (width, height) == (1200, 675)
    with Image.open(BytesIO(content)) as image:
        assert image.format == "WEBP"
        assert not image.getexif()
    assert len(content) < len(image_bytes())


def test_invalid_image_is_rejected() -> None:
    with pytest.raises(OSError, match="cannot identify image"):
        optimize_image(b"<html>Access denied</html>", 1200)


def test_s3_conditional_upload_and_existing_content() -> None:
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    expected = {
        "Bucket": "test-product-images",
        "Key": ANY,
        "Body": ANY,
        "ContentType": "image/webp",
        "CacheControl": "public, max-age=31536000, immutable",
        "IfNoneMatch": "*",
    }
    with Stubber(client) as stubber:
        stubber.add_response("put_object", {}, expected)
        stubber.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
            expected_params=expected,
        )
        store = S3ImageStore(settings(), client)
        first = store.put(image_bytes())
        second = store.put(image_bytes())
        assert first == second
        assert str(first[0]).startswith("https://images.example.com/products/webp-v1/")
        stubber.assert_no_pending_responses()


def test_s3_permission_failure_is_not_treated_as_existing_object() -> None:
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "put_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(ClientError):
            S3ImageStore(settings(), client).put(image_bytes())


def test_download_rejects_unapproved_host_without_network() -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client,
        pytest.raises(ValueError, match="retailer hosts"),
    ):
        download_image(AnyHttpUrl("https://127.0.0.1/secret"), settings(), client)


def test_download_limits_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "storage.images.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )
    url = product().image_urls[0]
    responses = [
        httpx.Response(302, headers={"location": "https://127.0.0.1/secret"}),
        httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>"),
        httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 2048),
    ]
    for response in responses:
        with (
            httpx.Client(
                transport=httpx.MockTransport(lambda request, response=response: response)
            ) as client,
            pytest.raises((ValueError, httpx.HTTPStatusError)),
        ):
            download_image(url, settings(image_max_bytes=1024), client)


def test_media_pipeline_persistence_preserves_images_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("storage.images.download_image", lambda *args: image_bytes())
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    monkeypatch.setattr("storage.images.boto3.client", lambda *args, **kwargs: client)
    report = reconcile_products([product()])
    with Stubber(client) as stubber:
        stubber.add_response("put_object", {})
        media = upload_report_images(report, settings(image_upload_enabled=True))
        stubber.assert_no_pending_responses()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        PipelineRepository.persist_reconciliation_report(session, report, media)
        session.commit()
        canonical = session.scalar(select(ProductModel))
        assert canonical is not None
        assert canonical.attributes["display_title"] == product().title
        assert canonical.attributes["image_url"].startswith("https://images.example.com/")
        original_url = canonical.attributes["image_url"]
        PipelineRepository.persist_reconciliation_report(session, report, {})
        session.commit()
        assert canonical.attributes["image_url"] == original_url
        updated_offer = report.clusters[0].offers[0].model_copy(update={"title": "Updated title"})
        updated_cluster = report.clusters[0].model_copy(update={"offers": (updated_offer,)})
        updated = report.model_copy(update={"clusters": (updated_cluster,)})
        PipelineRepository.persist_reconciliation_report(session, updated)
        session.commit()
        offer = session.scalar(select(OfferModel))
        assert offer is not None
        assert offer.title == "Updated title"


def test_failed_image_does_not_discard_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object) -> bytes:
        raise ValueError("Invalid image")

    monkeypatch.setattr("storage.images.download_image", unavailable)
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    monkeypatch.setattr("storage.images.boto3.client", lambda *args, **kwargs: client)
    with Stubber(client):
        assert (
            upload_report_images(
                reconcile_products([product()]), settings(image_upload_enabled=True)
            )
            == {}
        )
