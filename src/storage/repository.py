"""Repository providing transactional persistence for product clusters and offers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from storage.models import (
    OfferModel,
    ProductIdentifierModel,
    ProductModel,
    RetailerProductModel,
)

if TYPE_CHECKING:
    from matching.reconciliation import CanonicalVariantCluster, ReconciliationReport
    from storage.images import StoredImage


class PipelineRepository:
    """Transactional repository for pipeline storage."""

    @classmethod
    def persist_reconciliation_report(
        cls,
        session: Session,
        report: ReconciliationReport,
        media: Mapping[str, tuple[StoredImage, ...]] | None = None,
    ) -> dict[str, int]:
        """Persist a ReconciliationReport with idempotent upserts and append-only price history."""
        products_count = 0
        offers_count = 0

        # Imported lazily so the storage module remains usable while matching
        # contracts are still being initialized.
        from matching.reconciliation import merge_catalog_attributes

        for cluster in report.clusters:
            canonical_product_id = (
                cls._existing_canonical_product_id(session, cluster) or cluster.cluster_id
            )
            attributes = dict(cluster.attributes)
            if cluster.offers:
                first_offer = cluster.offers[0]
                attributes["display_title"] = first_offer.title
                attributes["title_source_url"] = str(first_offer.source_url)
                attributes["title_observed_at"] = first_offer.observed_at.isoformat()
                attributes["source_image_urls"] = [str(url) for url in first_offer.image_urls]
            images = (media or {}).get(cluster.cluster_id, ())
            if images:
                attributes["image_url"] = str(images[0].url)
                attributes["images"] = [image.model_dump(mode="json") for image in images]
            # 1. Upsert Canonical Product
            product_stmt = select(ProductModel).where(ProductModel.id == canonical_product_id)
            product = session.scalar(product_stmt)

            if product is None:
                product = ProductModel(
                    id=canonical_product_id,
                    category=cluster.category,
                    brand=cluster.brand,
                    model_name=cluster.model_name,
                    family=cluster.fingerprint.family,
                    chip=cluster.fingerprint.chip,
                    ram_gb=cluster.fingerprint.ram_gb,
                    storage_gb=cluster.fingerprint.storage_gb,
                    screen_size_inches=cluster.fingerprint.screen_size_inches,
                    gpu_model=cluster.fingerprint.gpu_model,
                    attributes=attributes,
                )
                session.add(product)
                products_count += 1
            else:
                attributes = merge_catalog_attributes(product.attributes, attributes)
                product.category = cluster.category
                product.brand = cluster.brand
                product.model_name = cluster.model_name
                product.family = cluster.fingerprint.family
                product.chip = cluster.fingerprint.chip
                product.ram_gb = cluster.fingerprint.ram_gb
                product.storage_gb = cluster.fingerprint.storage_gb
                product.screen_size_inches = cluster.fingerprint.screen_size_inches
                product.gpu_model = cluster.fingerprint.gpu_model
                # Preserve previously uploaded images if this crawl has none or upload failed.
                for key in ("image_url", "images"):
                    if key not in attributes and key in product.attributes:
                        attributes[key] = product.attributes[key]
                product.attributes = attributes
                product.updated_at = datetime.now(UTC)

            # Flush so product.id is committed for foreign keys
            session.flush()

            # 2. Upsert Linked Retailer Products & Offers
            for offer_item in cluster.offers:
                ret_stmt = select(RetailerProductModel).where(
                    RetailerProductModel.source == offer_item.source,
                    RetailerProductModel.source_product_id == offer_item.source_product_id,
                )
                ret_product = session.scalar(ret_stmt)

                if ret_product is None:
                    ret_product = RetailerProductModel(
                        canonical_product_id=canonical_product_id,
                        source=offer_item.source,
                        source_product_id=offer_item.source_product_id,
                        source_url=str(offer_item.source_url),
                        title=offer_item.title,
                        brand=cluster.brand,
                        model_name=cluster.model_name,
                        attributes={
                            **cluster.attributes,
                            "source_image_urls": [str(url) for url in offer_item.image_urls],
                        },
                        quality_status=offer_item.quality_status.value
                        if hasattr(offer_item.quality_status, "value")
                        else str(offer_item.quality_status),
                        quality_score=offer_item.quality_score,
                        quality_flags=list(offer_item.quality_flags),
                        lifecycle_status="ACTIVE" if offer_item.in_stock else "UNAVAILABLE",
                        missed_crawls=0,
                        first_seen_at=offer_item.observed_at,
                        last_seen_at=offer_item.observed_at,
                    )
                    session.add(ret_product)
                    session.flush()
                else:
                    ret_product.canonical_product_id = canonical_product_id
                    ret_product.last_seen_at = offer_item.observed_at
                    ret_product.title = offer_item.title
                    ret_product.source_url = str(offer_item.source_url)
                    ret_product.attributes = {
                        **merge_catalog_attributes(
                            ret_product.attributes,
                            cluster.attributes,
                        ),
                        "source_image_urls": [str(url) for url in offer_item.image_urls],
                    }
                    ret_product.model_name = cluster.model_name
                    ret_product.quality_status = (
                        offer_item.quality_status.value
                        if hasattr(offer_item.quality_status, "value")
                        else str(offer_item.quality_status)
                    )
                    ret_product.quality_score = offer_item.quality_score
                    ret_product.quality_flags = list(offer_item.quality_flags)
                    ret_product.lifecycle_status = (
                        "ACTIVE" if offer_item.in_stock else "UNAVAILABLE"
                    )
                    ret_product.missed_crawls = 0
                    ret_product.updated_at = datetime.now(UTC)

                # 3. Upsert Offer & Check Price History
                offer_stmt = select(OfferModel).where(
                    OfferModel.retailer_product_id == ret_product.id
                )
                offer_model = session.scalar(offer_stmt)

                if offer_model is None:
                    offer_model = OfferModel(
                        retailer_product_id=ret_product.id,
                        canonical_product_id=canonical_product_id,
                        source=offer_item.source,
                        source_product_id=offer_item.source_product_id,
                        source_url=str(offer_item.source_url),
                        title=offer_item.title,
                        price_paise=offer_item.price_paise,
                        mrp_paise=offer_item.mrp_paise,
                        coupon_price_paise=offer_item.coupon_price_paise,
                        in_stock=offer_item.in_stock,
                        quality_status=offer_item.quality_status.value
                        if hasattr(offer_item.quality_status, "value")
                        else str(offer_item.quality_status),
                        lifecycle_status="ACTIVE" if offer_item.in_stock else "UNAVAILABLE",
                        missed_crawls=0,
                        seller=offer_item.seller,
                        rating=offer_item.rating,
                        review_count=offer_item.review_count,
                        observed_at=offer_item.observed_at,
                    )
                    session.add(offer_model)
                    session.flush()
                    offers_count += 1
                else:
                    offer_model.canonical_product_id = canonical_product_id
                    offer_model.title = offer_item.title
                    offer_model.source_url = str(offer_item.source_url)
                    offer_model.price_paise = offer_item.price_paise
                    offer_model.mrp_paise = offer_item.mrp_paise
                    offer_model.coupon_price_paise = offer_item.coupon_price_paise
                    offer_model.in_stock = offer_item.in_stock
                    offer_model.quality_status = (
                        offer_item.quality_status.value
                        if hasattr(offer_item.quality_status, "value")
                        else str(offer_item.quality_status)
                    )
                    offer_model.lifecycle_status = (
                        "ACTIVE" if offer_item.in_stock else "UNAVAILABLE"
                    )
                    offer_model.missed_crawls = 0
                    offer_model.seller = offer_item.seller
                    offer_model.rating = offer_item.rating
                    offer_model.review_count = offer_item.review_count
                    offer_model.observed_at = offer_item.observed_at
                    offer_model.updated_at = datetime.now(UTC)
                    offers_count += 1

                # 4. Extract & Upsert Product Identifiers (MPN, GTIN, EAN, ASIN)
                identifiers_to_add: set[tuple[str, str]] = set()
                if offer_item.source.lower() == "amazon" and offer_item.source_product_id:
                    identifiers_to_add.add(("ASIN", offer_item.source_product_id.strip()))

                for id_key, id_type in (
                    ("gtin", "GTIN"),
                    ("ean", "EAN"),
                    ("mpn", "MPN"),
                    ("model number", "MPN"),
                    ("item model number", "MPN"),
                    ("asin", "ASIN"),
                ):
                    val = cluster.attributes.get(id_key) or (
                        ret_product.attributes.get(id_key) if ret_product else None
                    )
                    if val and str(val).strip():
                        clean_v = str(val).strip()
                        if 3 <= len(clean_v) <= 64:
                            identifiers_to_add.add((id_type, clean_v))

                for id_type, id_val in sorted(identifiers_to_add):
                    id_stmt = select(ProductIdentifierModel).where(
                        ProductIdentifierModel.identifier_type == id_type,
                        ProductIdentifierModel.identifier_value == id_val,
                    )
                    existing_id = session.scalar(id_stmt)
                    if existing_id is None:
                        id_entry = ProductIdentifierModel(
                            id=f"id_{uuid.uuid4().hex[:16]}",
                            canonical_product_id=canonical_product_id,
                            identifier_type=id_type,
                            identifier_value=id_val,
                            source=offer_item.source,
                            confidence=1.0,
                        )
                        session.add(id_entry)
                        session.flush()

            # Imported lazily to keep storage package initialization acyclic.
            from retrieval.documents import build_canonical_product_document
            from retrieval.repository import ProductRetrievalRepository

            ProductRetrievalRepository(session).upsert_document(
                build_canonical_product_document(product)
            )

        return {
            "products_persisted": products_count,
            "offers_persisted": offers_count,
        }

    @staticmethod
    def _existing_canonical_product_id(
        session: Session,
        cluster: CanonicalVariantCluster,
    ) -> str | None:
        """Keep a retailer listing attached to its established canonical ID."""
        candidates: list[str] = []
        for offer in cluster.offers:
            stmt = select(RetailerProductModel.canonical_product_id).where(
                RetailerProductModel.source == offer.source,
                RetailerProductModel.source_product_id == offer.source_product_id,
            )
            existing = session.scalar(stmt)
            if existing:
                candidates.append(existing)
        if not candidates:
            return None
        return sorted(set(candidates), key=lambda item: (-candidates.count(item), item))[0]

    @classmethod
    def get_canonical_product(
        cls,
        session: Session,
        product_id: str,
    ) -> ProductModel | None:
        """Retrieve a canonical product by id with all active offers."""
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        return session.scalar(stmt)

    @classmethod
    def update_offer_lifecycle(
        cls,
        session: Session,
        *,
        retailer_product_id: int,
        lifecycle_status: str,
        missed_crawls: int,
    ) -> None:
        """Update lifecycle status and missed crawl counter for a retailer product and offer."""
        ret_product = session.get(RetailerProductModel, retailer_product_id)
        if ret_product is not None:
            ret_product.lifecycle_status = lifecycle_status
            ret_product.missed_crawls = missed_crawls
            ret_product.updated_at = datetime.now(UTC)

        offer_stmt = select(OfferModel).where(OfferModel.retailer_product_id == retailer_product_id)
        offer_model = session.scalar(offer_stmt)
        if offer_model is not None:
            offer_model.lifecycle_status = lifecycle_status
            offer_model.missed_crawls = missed_crawls
            offer_model.updated_at = datetime.now(UTC)
        session.flush()

    @classmethod
    def get_lifecycle_distribution(cls, session: Session) -> dict[str, int]:
        """Aggregate count of retailer products by lifecycle status."""
        session.flush()
        stmt = select(
            RetailerProductModel.lifecycle_status,
            func.count(RetailerProductModel.id),
        ).group_by(RetailerProductModel.lifecycle_status)
        results = session.execute(stmt).all()
        dist: dict[str, int] = {
            "ACTIVE": 0,
            "STALE": 0,
            "UNAVAILABLE": 0,
            "DISCONTINUED": 0,
        }
        for status_val, count_val in results:
            if status_val in dist:
                dist[status_val] = count_val
            else:
                dist[status_val] = count_val
        return dist
