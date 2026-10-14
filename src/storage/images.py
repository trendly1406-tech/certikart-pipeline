"""Bounded product image processing and immutable S3 storage, outside DB transactions."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
from collections.abc import Iterable
from io import BytesIO
from typing import TYPE_CHECKING

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field

from config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

    from matching.reconciliation import ReconciliationReport


class StoredImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: AnyHttpUrl
    key: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_url: AnyHttpUrl
    source: str
    source_product_id: str
    observed_at: AwareDatetime
    transformation: str = "webp-v1"


class ImageUploadCandidate(BaseModel):
    """One canonical product and the retailer images selected as its gallery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_product_id: str
    source: str
    source_product_id: str
    observed_at: AwareDatetime
    image_urls: tuple[AnyHttpUrl, ...]


def validate_download_url(url: AnyHttpUrl, allowed_hosts: tuple[str, ...]) -> None:
    """Only HTTPS retailer image hosts with publicly routable DNS answers are accepted."""
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.port != 443
        or url.host not in allowed_hosts
    ):
        raise ValueError("Image URL is outside the configured retailer hosts")
    addresses = socket.getaddrinfo(url.host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
        raise ValueError("Image host must resolve to public IP addresses")


def download_image(url: AnyHttpUrl, settings: Settings, client: httpx.Client) -> bytes:
    validate_download_url(url, settings.image_allowed_hosts)
    # No redirects: a retailer CDN must not redirect this fetch to an internal resource.
    with client.stream("GET", str(url), follow_redirects=False) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Unsupported image content type")
        length = response.headers.get("content-length")
        if length and int(length) > settings.image_max_bytes:
            raise ValueError("Image exceeds download limit")
        content = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            content.extend(chunk)
            if len(content) > settings.image_max_bytes:
                raise ValueError("Image exceeds download limit")
        return bytes(content)


def optimize_image(content: bytes, max_dimension: int) -> tuple[bytes, int, int]:
    with Image.open(BytesIO(content), formats=["JPEG", "PNG", "WEBP"]) as original:
        if original.width * original.height > 25_000_000:
            raise ValueError("Image exceeds pixel limit")
        original.load()
        oriented = ImageOps.exif_transpose(original)
        converted = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
        converted.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        # A fresh image removes EXIF, GPS, comments and other source metadata.
        clean = Image.new(converted.mode, converted.size)
        clean.paste(converted)
        output = BytesIO()
        clean.save(output, format="WEBP", quality=82, method=6)
        return output.getvalue(), clean.width, clean.height


class S3ImageStore:
    def __init__(self, settings: Settings, client: S3Client | None = None) -> None:
        if not settings.image_storage_bucket:
            raise ValueError("Configure image S3 bucket")
        self.settings = settings
        self.client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                region_name=settings.image_s3_region,
                endpoint_url=(
                    str(settings.image_s3_endpoint_url).rstrip("/")
                    if settings.image_s3_endpoint_url
                    else None
                ),
                aws_access_key_id=settings.image_s3_access_key_id.get_secret_value() or None,
                aws_secret_access_key=settings.image_s3_secret_access_key.get_secret_value()
                or None,
                aws_session_token=settings.image_s3_session_token.get_secret_value() or None,
                config=Config(
                    connect_timeout=5,
                    read_timeout=15,
                    retries={"max_attempts": 2, "mode": "standard"},
                    s3={
                        "addressing_style": (
                            "path" if settings.image_s3_force_path_style else "auto"
                        )
                    },
                ),
            )
        )

    def put(self, content: bytes) -> tuple[AnyHttpUrl, str, str, int, int]:
        optimized, width, height = optimize_image(content, self.settings.image_max_dimension)
        digest = hashlib.sha256(optimized).hexdigest()
        key = f"{self.settings.image_s3_prefix.strip('/')}/webp-v1/{digest}.webp"
        try:
            self.client.put_object(
                Bucket=self.settings.image_storage_bucket,
                Key=key,
                Body=optimized,
                ContentType="image/webp",
                CacheControl="public, max-age=31536000, immutable",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
        base = str(self.settings.image_delivery_base_url).rstrip("/")
        return AnyHttpUrl(f"{base}/{key}"), key, digest, width, height


class SupabaseImageStore:
    """Upload public product assets through Supabase Storage's server REST API."""

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        if settings.image_supabase_url is None:
            raise ValueError("Configure a Supabase project URL")
        if not settings.image_storage_bucket:
            raise ValueError("Configure an image storage bucket")
        secret = settings.image_supabase_secret_key.get_secret_value()
        if not secret:
            raise ValueError("Configure a Supabase server-side secret key")
        self.settings = settings
        self.client = client
        self.secret = secret

    def put(self, content: bytes) -> tuple[AnyHttpUrl, str, str, int, int]:
        optimized, width, height = optimize_image(content, self.settings.image_max_dimension)
        digest = hashlib.sha256(optimized).hexdigest()
        key = f"{self.settings.image_s3_prefix.strip('/')}/webp-v1/{digest}.webp"
        project_url = str(self.settings.image_supabase_url).rstrip("/")
        response = self.client.post(
            f"{project_url}/storage/v1/object/{self.settings.image_storage_bucket}/{key}",
            headers={
                "Authorization": f"Bearer {self.secret}",
                "apikey": self.secret,
                "Content-Type": "image/webp",
                "Cache-Control": "public, max-age=31536000, immutable",
                "x-upsert": "true",
            },
            content=optimized,
        )
        response.raise_for_status()
        base = str(self.settings.image_delivery_base_url).rstrip("/")
        return AnyHttpUrl(f"{base}/{key}"), key, digest, width, height


def upload_report_images(
    report: ReconciliationReport,
    settings: Settings | None = None,
) -> dict[str, tuple[StoredImage, ...]]:
    candidates = (
        ImageUploadCandidate(
            canonical_product_id=cluster.cluster_id,
            source=cluster.offers[0].source,
            source_product_id=cluster.offers[0].source_product_id,
            observed_at=cluster.offers[0].observed_at,
            image_urls=cluster.offers[0].image_urls,
        )
        for cluster in report.clusters
        if cluster.offers
    )
    return upload_image_candidates(candidates, settings)


def upload_image_candidates(
    candidates: Iterable[ImageUploadCandidate],
    settings: Settings | None = None,
) -> dict[str, tuple[StoredImage, ...]]:
    """Download, optimize and upload bounded galleries for canonical products."""
    settings = settings or get_settings()
    if not settings.image_upload_enabled:
        return {}
    result: dict[str, tuple[StoredImage, ...]] = {}
    cache: dict[str, tuple[AnyHttpUrl, str, str, int, int]] = {}
    with httpx.Client(timeout=15, trust_env=False) as client:
        store: S3ImageStore | SupabaseImageStore = (
            SupabaseImageStore(settings, client)
            if settings.image_supabase_url is not None
            else S3ImageStore(settings)
        )
        for candidate in candidates:
            stored: list[StoredImage] = []
            for url in candidate.image_urls[: settings.image_max_per_product]:
                try:
                    if str(url) not in cache:
                        cache[str(url)] = store.put(download_image(url, settings, client))
                    public_url, key, digest, width, height = cache[str(url)]
                    if any(image.sha256 == digest for image in stored):
                        continue
                    stored.append(
                        StoredImage(
                            url=public_url,
                            key=key,
                            sha256=digest,
                            width=width,
                            height=height,
                            source_url=url,
                            source=candidate.source,
                            source_product_id=candidate.source_product_id,
                            observed_at=candidate.observed_at,
                        )
                    )
                except (
                    httpx.HTTPError,
                    BotoCoreError,
                    ClientError,
                    OSError,
                    ValueError,
                    UnidentifiedImageError,
                    Image.DecompressionBombError,
                ) as error:
                    # URLs and credentials must not enter logs. A media failure does not
                    # discard otherwise useful product data; a later run can retry it.
                    logging.getLogger(__name__).warning(
                        "Product image skipped for %s (%s)",
                        candidate.canonical_product_id,
                        type(error).__name__,
                    )
            if stored:
                result[candidate.canonical_product_id] = tuple(stored)
    return result
