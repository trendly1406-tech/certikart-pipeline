"""Cross-source product reconciliation, exact-variant deduplication, and offer linking."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from categories import AttributeValue
from categories.registry import get_category
from matching.fingerprint import ProductFingerprint
from matching.matcher import MatchDecision, compare_products
from quality import DataQualityClassifier, QualityReport, QualityStatus

if TYPE_CHECKING:
    from sources.contracts import ParsedProduct


class LinkedOffer(BaseModel):
    """One retailer's live product offer linked to a canonical variant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    source_product_id: str = Field(min_length=1)
    source_url: AnyHttpUrl
    title: str = Field(min_length=1)
    image_urls: tuple[AnyHttpUrl, ...] = Field(default=(), max_length=6)
    price_paise: int = Field(ge=0)
    mrp_paise: int | None = Field(default=None, ge=0)
    coupon_price_paise: int | None = Field(default=None, ge=0)
    in_stock: bool
    seller: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    quality_status: QualityStatus = QualityStatus.VALID
    quality_score: int = Field(default=100, ge=0, le=100)
    quality_flags: tuple[str, ...] = Field(default_factory=tuple)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @classmethod
    def from_parsed_product(
        cls,
        product: ParsedProduct,
        quality_report: QualityReport | None = None,
    ) -> LinkedOffer:
        if quality_report is None:
            quality_report = DataQualityClassifier.classify(product)
        return cls(
            source=product.source,
            source_product_id=product.source_product_id,
            source_url=product.source_url,
            title=product.title,
            image_urls=product.image_urls,
            price_paise=product.price_paise,
            mrp_paise=product.mrp_paise,
            coupon_price_paise=product.coupon_price_paise,
            in_stock=product.in_stock,
            seller=product.seller,
            rating=product.rating,
            review_count=product.review_count,
            quality_status=quality_report.status,
            quality_score=quality_report.score,
            quality_flags=quality_report.flags,
            observed_at=product.observed_at,
        )


def _generate_cluster_id(fingerprint: ProductFingerprint) -> str:
    payload: dict[str, object]
    if fingerprint.gtin:
        payload = {"category": fingerprint.category, "gtin": fingerprint.gtin}
    elif fingerprint.manufacturer_part_number:
        payload = {
            "category": fingerprint.category,
            "brand": fingerprint.brand,
            "manufacturer_part_number": fingerprint.manufacturer_part_number,
        }
    else:
        category = get_category(fingerprint.category)
        identity_attributes = {
            key: fingerprint.value_for(key) for key in category.identity_attributes
        }
        payload = {
            "category": fingerprint.category,
            "brand": fingerprint.brand,
            "family": fingerprint.family,
            "model_name": fingerprint.model_name,
            "identity_attributes": identity_attributes,
        }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return f"clus_{hashlib.sha256(encoded).hexdigest()[:16]}"


def merge_catalog_attributes(
    current: dict[str, AttributeValue],
    incoming: dict[str, AttributeValue],
) -> dict[str, AttributeValue]:
    """Merge display evidence without changing the product identity fingerprint.

    Exact retailer matches often expose different portions of the same specification
    table. Nested maps such as ``spec_sections`` are combined recursively, while a
    later non-empty scalar remains the fresher observation.
    """
    merged = dict(current)
    for key, incoming_value in incoming.items():
        current_value = merged.get(key)
        if isinstance(current_value, dict) and isinstance(incoming_value, dict):
            merged[key] = _merge_nested_maps(current_value, incoming_value)
        elif incoming_value not in ("", [], {}):
            merged[key] = incoming_value
    return merged


def _merge_nested_maps(
    current: dict[str, object],
    incoming: dict[str, object],
) -> dict[str, object]:
    merged = dict(current)
    for key, incoming_value in incoming.items():
        current_value = merged.get(key)
        if isinstance(current_value, dict) and isinstance(incoming_value, dict):
            merged[key] = _merge_nested_maps(current_value, incoming_value)
        elif incoming_value not in (None, "", [], {}):
            merged[key] = incoming_value
    return merged


class CanonicalVariantCluster(BaseModel):
    """A canonical exact-variant entity grouping matching offers across retailers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    attributes: dict[str, AttributeValue]
    fingerprint: ProductFingerprint
    offers: tuple[LinkedOffer, ...] = Field(default_factory=tuple)
    review_candidates: tuple[LinkedOffer, ...] = Field(default_factory=tuple)
    best_price_paise: int | None = Field(default=None, ge=0)
    best_source: str | None = None
    max_price_paise: int | None = Field(default=None, ge=0)
    price_spread_paise: int | None = Field(default=None, ge=0)
    savings_paise: int | None = Field(default=None, ge=0)

    @classmethod
    def from_fingerprint(
        cls,
        fingerprint: ProductFingerprint,
        attributes: dict[str, AttributeValue] | None = None,
        offers: Sequence[LinkedOffer] = (),
        review_candidates: Sequence[LinkedOffer] = (),
    ) -> CanonicalVariantCluster:
        cluster_id = _generate_cluster_id(fingerprint)
        in_stock_offers = [o for o in offers if o.in_stock]
        best_price: int | None = None
        best_source: str | None = None
        max_price: int | None = None
        spread: int | None = None
        savings: int | None = None

        if in_stock_offers:
            sorted_by_price = sorted(in_stock_offers, key=lambda o: o.price_paise)
            best_offer = sorted_by_price[0]
            highest_offer = sorted_by_price[-1]
            best_price = best_offer.price_paise
            best_source = best_offer.source
            max_price = highest_offer.price_paise
            spread = max_price - best_price
            savings = spread if len(in_stock_offers) > 1 else 0
        elif offers:
            sorted_all = sorted(offers, key=lambda o: o.price_paise)
            best_offer = sorted_all[0]
            highest_offer = sorted_all[-1]
            best_price = best_offer.price_paise
            best_source = best_offer.source
            max_price = highest_offer.price_paise
            spread = max_price - best_price
            savings = spread if len(offers) > 1 else 0

        return cls(
            cluster_id=cluster_id,
            category=fingerprint.category,
            brand=fingerprint.brand,
            model_name=fingerprint.model_name,
            attributes=dict(attributes if attributes is not None else fingerprint.attributes),
            fingerprint=fingerprint,
            offers=tuple(offers),
            review_candidates=tuple(review_candidates),
            best_price_paise=best_price,
            best_source=best_source,
            max_price_paise=max_price,
            price_spread_paise=spread,
            savings_paise=savings,
        )


class SourceReconciliationStats(BaseModel):
    """Detailed reconciliation breakdown for a single retailer source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_count: int = Field(ge=0)
    matched_in_multi_source_cluster: int = Field(ge=0)
    single_source_canonical_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    rejected_count: int = Field(default=0, ge=0)


class ReconciliationReport(BaseModel):
    """Summary of cross-source product deduplication and linking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_collected: int = Field(ge=0)
    total_clusters: int = Field(ge=0)
    multi_source_clusters: int = Field(ge=0)
    review_required_count: int = Field(ge=0)
    total_rejected: int = Field(default=0, ge=0)
    source_breakdown: dict[str, SourceReconciliationStats] = Field(default_factory=dict)
    clusters: tuple[CanonicalVariantCluster, ...]
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


def reconcile_products(products: Sequence[ParsedProduct]) -> ReconciliationReport:
    """Deduplicate and link products from multiple sources into canonical clusters."""
    if not products:
        return ReconciliationReport(
            total_collected=0,
            total_clusters=0,
            multi_source_clusters=0,
            review_required_count=0,
            total_rejected=0,
            source_breakdown={},
            clusters=(),
            generated_at=datetime.now(UTC),
        )

    accumulators: list[_ClusterAccumulator] = []
    source_stats: dict[str, dict[str, int]] = {}
    total_rejected = 0

    for p in products:
        s = p.source.casefold().strip()
        if s not in source_stats:
            source_stats[s] = {
                "collected": 0,
                "multi": 0,
                "single": 0,
                "review": 0,
                "rejected": 0,
            }
        source_stats[s]["collected"] += 1

    for product in products:
        q_report = DataQualityClassifier.classify(product)
        offer = LinkedOffer.from_parsed_product(product, q_report)
        s_name = product.source.casefold().strip()

        # Reject accessories / disqualified listings from polluting canonical clusters
        if q_report.status == QualityStatus.REJECTED:
            if s_name in source_stats:
                source_stats[s_name]["rejected"] += 1
            total_rejected += 1
            continue

        candidate_fp = product.to_fingerprint()
        matched = False
        review_matched = False

        for acc in accumulators:
            result = compare_products(acc.fingerprint, candidate_fp)

            if result.decision is MatchDecision.MATCH:
                acc.offers.append(offer)
                acc.attributes = merge_catalog_attributes(acc.attributes, product.attributes)
                matched = True
                break
            elif result.decision is MatchDecision.REVIEW and not review_matched:
                acc.review_candidates.append(offer)
                review_matched = True

        if not matched and not review_matched:
            accumulators.append(_ClusterAccumulator(candidate_fp, product.attributes, offer))

    final_clusters: list[CanonicalVariantCluster] = []
    multi_source_count = 0
    total_reviews = 0

    for acc in accumulators:
        cluster = CanonicalVariantCluster.from_fingerprint(
            fingerprint=acc.fingerprint,
            attributes=acc.attributes,
            offers=acc.offers,
            review_candidates=acc.review_candidates,
        )
        final_clusters.append(cluster)

        distinct_sources = {o.source.casefold().strip() for o in cluster.offers}
        is_multi = len(distinct_sources) > 1
        if is_multi:
            multi_source_count += 1
        total_reviews += len(cluster.review_candidates)

        for off in cluster.offers:
            s_name = off.source.casefold().strip()
            if s_name in source_stats:
                if is_multi:
                    source_stats[s_name]["multi"] += 1
                else:
                    source_stats[s_name]["single"] += 1

        for rev in cluster.review_candidates:
            s_name = rev.source.casefold().strip()
            if s_name in source_stats:
                source_stats[s_name]["review"] += 1

    breakdown = {
        s: SourceReconciliationStats(
            collected_count=data["collected"],
            matched_in_multi_source_cluster=data["multi"],
            single_source_canonical_count=data["single"],
            review_count=data["review"],
            rejected_count=data["rejected"],
        )
        for s, data in sorted(source_stats.items())
    }

    return ReconciliationReport(
        total_collected=len(products),
        total_clusters=len(final_clusters),
        multi_source_clusters=multi_source_count,
        review_required_count=total_reviews,
        total_rejected=total_rejected,
        source_breakdown=breakdown,
        clusters=tuple(final_clusters),
        generated_at=datetime.now(UTC),
    )


class _ClusterAccumulator:
    __slots__ = ("attributes", "fingerprint", "offers", "review_candidates")

    def __init__(
        self,
        fingerprint: ProductFingerprint,
        attributes: dict[str, AttributeValue],
        first_offer: LinkedOffer,
    ) -> None:
        self.fingerprint = fingerprint
        self.attributes = dict(attributes)
        self.offers: list[LinkedOffer] = [first_offer]
        self.review_candidates: list[LinkedOffer] = []
