"""Stable boundary implemented by every permitted source adapter."""

from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Protocol, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from categories import AttributeValue, get_category, get_subcategory
from matching.fingerprint import ProductFingerprint
from pricing import PriceObservation
from reviews import ReviewCollectionPage


class RawSourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    source_product_id: str = Field(min_length=1)
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    subcategory: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    source_url: AnyHttpUrl
    observed_at: datetime
    payload: Mapping[str, object]
    content_hash: str = Field(min_length=16)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("category")
    @classmethod
    def require_supported_category(cls, value: str) -> str:
        return get_category(value).code

    @model_validator(mode="after")
    def require_supported_subcategory(self) -> Self:
        if self.subcategory is not None:
            get_subcategory(self.category, self.subcategory)
        return self


class SourceProductReference(BaseModel):
    """Approved product URL supplied to a retailer adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_product_id: str = Field(min_length=1, max_length=128)
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    subcategory: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    source_url: AnyHttpUrl

    @field_validator("category")
    @classmethod
    def require_supported_category(cls, value: str) -> str:
        return get_category(value).code

    @model_validator(mode="after")
    def require_supported_subcategory(self) -> Self:
        if self.subcategory is not None:
            get_subcategory(self.category, self.subcategory)
        return self


class FetchedSourceDocument(BaseModel):
    """Transport output before it is attributed to a source product."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    payload: Mapping[str, object]
    content_hash: str = Field(min_length=16)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class ParsedProduct(BaseModel):
    """Normalized structured product representation extracted from raw source data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    source_product_id: str = Field(min_length=1)
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    subcategory: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    image_urls: tuple[AnyHttpUrl, ...] = Field(default=(), max_length=6)
    brand: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    manufacturer_part_number: str | None = None
    gtin: str | None = None
    price_paise: int = Field(ge=0)
    mrp_paise: int | None = Field(default=None, ge=0)
    coupon_price_paise: int | None = Field(default=None, ge=0)
    in_stock: bool = True
    seller: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    source_url: AnyHttpUrl
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("category")
    @classmethod
    def require_supported_category(cls, value: str) -> str:
        return get_category(value).code

    @model_validator(mode="after")
    def require_supported_subcategory(self) -> Self:
        if self.subcategory is not None:
            get_subcategory(self.category, self.subcategory)
        return self

    def to_fingerprint(self) -> ProductFingerprint:
        try:
            from categories.registry import get_category_handler

            handler = get_category_handler(self.category)
            return handler.normalize(self)
        except Exception:
            return ProductFingerprint(
                category=self.category,
                brand=self.brand,
                model_name=self.model_name,
                manufacturer_part_number=self.manufacturer_part_number,
                gtin=self.gtin,
                attributes=self.attributes,
            )

    def to_price_observation(self, offer_id: str | None = None) -> PriceObservation:
        resolved_offer_id = offer_id or f"{self.source}:{self.source_product_id}"
        return PriceObservation(
            offer_id=resolved_offer_id,
            price_paise=self.price_paise,
            mrp_paise=self.mrp_paise,
            coupon_price_paise=self.coupon_price_paise,
            in_stock=self.in_stock,
            seller=self.seller,
            observed_at=self.observed_at,
        )


class SourceTransport(Protocol):
    """Injected API, feed, or permitted-page transport."""

    async def fetch(self, source_url: AnyHttpUrl) -> FetchedSourceDocument: ...


class SourceAdapter(Protocol):
    name: str

    def discover(self) -> AsyncIterator[str]: ...

    async def fetch(self, source_product_id: str) -> RawSourceRecord: ...

    def parse(self, record: RawSourceRecord) -> ParsedProduct: ...


class ReviewSourceAdapter(Protocol):
    """Authorized, cursor-based review collection boundary."""

    name: str

    async def fetch_reviews(
        self,
        source_product_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> ReviewCollectionPage: ...
