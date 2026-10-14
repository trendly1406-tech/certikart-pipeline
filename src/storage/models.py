"""SQLAlchemy declarative models for canonical products, retailer listings, and price history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProductModel(Base):
    """Canonical product variant entity."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    brand: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    family: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    chip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screen_size_inches: Mapped[float | None] = mapped_column(Float, nullable=True)
    gpu_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    identifiers: Mapped[list[ProductIdentifierModel]] = relationship(
        "ProductIdentifierModel",
        back_populates="canonical_product",
        cascade="all, delete-orphan",
    )
    retailer_products: Mapped[list[RetailerProductModel]] = relationship(
        "RetailerProductModel",
        back_populates="canonical_product",
        cascade="all, delete-orphan",
    )
    offers: Mapped[list[OfferModel]] = relationship(
        "OfferModel",
        back_populates="canonical_product",
    )


class ProductDocumentModel(Base):
    """Stable semantic projection of one canonical product."""

    __tablename__ = "product_documents"
    __table_args__ = (UniqueConstraint("canonical_product_id", name="uq_product_document_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_product_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    document_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ProductEmbeddingModel(Base):
    """Provider-versioned vector for a canonical product document."""

    __tablename__ = "product_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "product_document_id",
            "provider",
            "model_name",
            name="uq_product_embedding_document_provider_model",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_product_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    product_document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("product_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class RetailerProductModel(Base):
    """Specific retailer product listing."""

    __tablename__ = "retailer_products"
    __table_args__ = (
        UniqueConstraint("source", "source_product_id", name="uq_retailer_product_source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_product_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_product_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    quality_status: Mapped[str] = mapped_column(
        String(32), default="VALID", index=True, nullable=False
    )
    quality_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", index=True, nullable=False
    )
    missed_crawls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    canonical_product: Mapped[ProductModel | None] = relationship(
        "ProductModel",
        back_populates="retailer_products",
    )
    current_offer: Mapped[OfferModel | None] = relationship(
        "OfferModel",
        back_populates="retailer_product",
        uselist=False,
        cascade="all, delete-orphan",
    )


class OfferModel(Base):
    """Latest live price and availability offer for a retailer listing."""

    __tablename__ = "offers"
    __table_args__ = (UniqueConstraint("retailer_product_id", name="uq_offer_retailer_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retailer_product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("retailer_products.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    canonical_product_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_product_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    price_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mrp_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    coupon_price_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quality_status: Mapped[str] = mapped_column(
        String(32), default="VALID", index=True, nullable=False
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", index=True, nullable=False
    )
    missed_crawls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seller: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    retailer_product: Mapped[RetailerProductModel] = relationship(
        "RetailerProductModel",
        back_populates="current_offer",
    )
    canonical_product: Mapped[ProductModel | None] = relationship(
        "ProductModel",
        back_populates="offers",
    )


class ProductImportRunModel(Base):
    """Durable status for one shopper-requested retailer product import."""

    __tablename__ = "product_import_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    source_product_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    progress_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_product_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    reused_fresh_listing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ProductIdentifierModel(Base):
    """Specific unique external identifier (MPN, GTIN, EAN, ASIN) linked to a canonical product."""

    __tablename__ = "product_identifiers"
    __table_args__ = (
        UniqueConstraint("identifier_type", "identifier_value", name="uq_identifier_type_value"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_product_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier_type: Mapped[str] = mapped_column(
        String(32), index=True, nullable=False
    )  # MPN, GTIN, EAN, UPC, ASIN
    identifier_value: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # amazon, flipkart, croma
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    canonical_product: Mapped[ProductModel] = relationship(
        "ProductModel",
        back_populates="identifiers",
    )
