"""Validated product-URL intake and durable single-product import orchestration."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from categories import get_category
from sources.common import extract_manufacturer_part_number
from storage.models import ProductImportRunModel, ProductModel, RetailerProductModel

if TYPE_CHECKING:
    from sources.contracts import ParsedProduct


class ProductImportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductImportTarget(BaseModel):
    """Canonical, approved retailer target derived from a shopper URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    source_product_id: str = Field(min_length=1, max_length=128)
    category: str
    source_url: AnyHttpUrl


class ProductImportRequest(BaseModel):
    """External worker payload for one idempotent product import run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=36, max_length=36)
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    product_url: AnyHttpUrl
    freshness_seconds: int = Field(default=14_400, ge=0, le=604_800)

    @field_validator("run_id")
    @classmethod
    def require_uuid(cls, value: str) -> str:
        parsed = UUID(value)
        if parsed.version != 4:
            raise ValueError("run_id must be a UUID4")
        return str(parsed)

    @field_validator("category")
    @classmethod
    def require_supported_category(cls, value: str) -> str:
        if value == "auto":
            return value
        return get_category(value).code

    @model_validator(mode="after")
    def validate_target(self) -> ProductImportRequest:
        parse_product_url(self.product_url, self.category)
        return self


class ProductImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: ProductImportStatus
    canonical_product_id: str | None = None
    reused_fresh_listing: bool = False
    failure_code: str | None = None


_AMAZON_ID = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE)
_FLIPKART_ID = re.compile(r"/p/(itm[a-zA-Z0-9]+)(?:[/?]|$)")
_CROMA_ID = re.compile(r"/p/(\d{5,12})(?:[/?]|$)")


def build_product_search_query(product: ParsedProduct) -> str:
    """Build a compact, variant-specific query for cross-retailer discovery."""
    if product.manufacturer_part_number:
        identity = product.manufacturer_part_number
    elif product.gtin:
        identity = product.gtin
    else:
        identity = extract_manufacturer_part_number(product.title) or product.model_name
    return " ".join(f"{product.brand} {identity}".split())


def build_product_search_queries(product: ParsedProduct) -> tuple[str, ...]:
    """Return bounded exact-first discovery queries for retailer search pages."""
    model_name = product.model_name.strip()
    brand = product.brand.strip()
    if model_name.casefold().startswith(brand.casefold() + " "):
        model_name = model_name[len(brand) :].strip()
    candidates = (
        build_product_search_query(product),
        " ".join(f"{brand} {model_name}".split()),
    )
    return tuple(dict.fromkeys(query for query in candidates if query))


def parse_product_url(product_url: AnyHttpUrl | str, category: str) -> ProductImportTarget:
    """Validate an approved retailer URL and extract its stable retailer product ID."""
    category_code = "auto" if category == "auto" else get_category(category).code
    parsed_url = AnyHttpUrl(str(product_url))
    if (
        parsed_url.scheme != "https"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.port not in (None, 443)
    ):
        raise ValueError("Product URL must use standard HTTPS without credentials")

    host = str(parsed_url.host).casefold()
    path = urlsplit(str(parsed_url)).path
    source: str
    match: re.Match[str] | None
    canonical_path: str

    if host in {"amazon.in", "www.amazon.in"}:
        source = "amazon"
        match = _AMAZON_ID.search(path)
        canonical_path = f"/dp/{match.group(1).upper()}" if match else ""
    elif host in {"flipkart.com", "www.flipkart.com"}:
        source = "flipkart"
        match = _FLIPKART_ID.search(path)
        canonical_path = path.rstrip("/")
    elif host in {"croma.com", "www.croma.com"}:
        source = "croma"
        match = _CROMA_ID.search(path)
        canonical_path = path.rstrip("/")
    else:
        raise ValueError("Unsupported retailer host")

    if match is None:
        raise ValueError(f"Could not identify a {source} product ID from the URL")

    source_product_id = match.group(1)
    if source == "amazon":
        source_product_id = source_product_id.upper()
    canonical_host = f"www.{source}.in" if source == "amazon" else f"www.{source}.com"
    canonical_url = urlunsplit(("https", canonical_host, canonical_path, "", ""))
    return ProductImportTarget(
        source=source,
        source_product_id=source_product_id,
        category=category_code,
        source_url=AnyHttpUrl(canonical_url),
    )


class ProductImportRepository:
    """Transactional state changes for product import runs and freshness reuse."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_get(
        self,
        request: ProductImportRequest,
        target: ProductImportTarget,
    ) -> ProductImportRunModel:
        existing = self.session.get(ProductImportRunModel, request.run_id)
        if existing is not None:
            if (
                existing.source != target.source
                or existing.source_product_id != target.source_product_id
                or (existing.category != target.category and request.category != "auto")
            ):
                raise ValueError("run_id is already assigned to a different product import")
            return existing

        now = datetime.now(UTC)
        run = ProductImportRunModel(
            id=request.run_id,
            source=target.source,
            source_product_id=target.source_product_id,
            source_url=str(target.source_url),
            category=target.category,
            status=ProductImportStatus.QUEUED.value,
            progress_stage="queued",
            requested_at=now,
            updated_at=now,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def find_fresh_product(
        self,
        target: ProductImportTarget,
        freshness_seconds: int,
    ) -> str | None:
        if freshness_seconds == 0:
            return None
        cutoff = datetime.now(UTC) - timedelta(seconds=freshness_seconds)
        stmt = (
            select(RetailerProductModel.canonical_product_id)
            .join(ProductModel, ProductModel.id == RetailerProductModel.canonical_product_id)
            .where(
                RetailerProductModel.source == target.source,
                RetailerProductModel.source_product_id == target.source_product_id,
                RetailerProductModel.canonical_product_id.is_not(None),
                RetailerProductModel.last_seen_at >= cutoff,
            )
        )
        if target.category != "auto":
            stmt = stmt.where(ProductModel.category == target.category)
        return self.session.scalar(stmt)

    def resolved_product_id(self, target: ProductImportTarget) -> str | None:
        stmt = (
            select(RetailerProductModel.canonical_product_id)
            .join(ProductModel, ProductModel.id == RetailerProductModel.canonical_product_id)
            .where(
                RetailerProductModel.source == target.source,
                RetailerProductModel.source_product_id == target.source_product_id,
                ProductModel.category == target.category,
            )
        )
        return self.session.scalar(stmt)

    def has_multiple_sources(self, canonical_product_id: str) -> bool:
        """Return whether a canonical product already has cross-retailer coverage."""
        stmt = select(func.count(func.distinct(RetailerProductModel.source))).where(
            RetailerProductModel.canonical_product_id == canonical_product_id
        )
        return (self.session.scalar(stmt) or 0) > 1

    def get(self, run_id: str) -> ProductImportRunModel | None:
        return self.session.get(ProductImportRunModel, run_id)

    def mark_running(self, run: ProductImportRunModel, stage: str) -> None:
        if run.status == ProductImportStatus.CANCELLED.value:
            return
        now = datetime.now(UTC)
        run.status = ProductImportStatus.RUNNING.value
        run.progress_stage = stage
        run.started_at = run.started_at or now
        run.updated_at = now
        self.session.flush()

    def update_stage(self, run: ProductImportRunModel, stage: str) -> None:
        if run.status != ProductImportStatus.CANCELLED.value:
            run.progress_stage = stage
            run.updated_at = datetime.now(UTC)
            self.session.flush()

    def mark_succeeded(
        self,
        run: ProductImportRunModel,
        canonical_product_id: str,
        *,
        reused_fresh_listing: bool,
    ) -> None:
        now = datetime.now(UTC)
        run.status = ProductImportStatus.SUCCEEDED.value
        run.progress_stage = "completed"
        run.canonical_product_id = canonical_product_id
        run.reused_fresh_listing = reused_fresh_listing
        run.failure_code = None
        run.failure_message = None
        run.completed_at = now
        run.updated_at = now
        self.session.flush()

    def mark_failed(self, run: ProductImportRunModel, code: str, message: str) -> None:
        now = datetime.now(UTC)
        run.status = ProductImportStatus.FAILED.value
        run.progress_stage = "failed"
        run.failure_code = code
        run.failure_message = message[:500]
        run.completed_at = now
        run.updated_at = now
        self.session.flush()

    def cancel(self, run_id: str) -> bool:
        run = self.session.get(ProductImportRunModel, run_id)
        if run is None or run.status in {
            ProductImportStatus.SUCCEEDED.value,
            ProductImportStatus.FAILED.value,
            ProductImportStatus.CANCELLED.value,
        }:
            return False
        now = datetime.now(UTC)
        run.status = ProductImportStatus.CANCELLED.value
        run.progress_stage = "cancelled"
        run.completed_at = now
        run.updated_at = now
        self.session.flush()
        return True
