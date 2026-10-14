"""100-point explainable product identity confidence scoring engine."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from matching.fingerprint import ProductFingerprint


class MatchConfidenceTier(StrEnum):
    """Deterministic match confidence tiers."""

    MATCH = "MATCH"  # 95 - 100: Deterministic Canonical Match
    STRONG_MATCH = "STRONG_MATCH"  # 85 - 94: High Confidence Match
    REVIEW = "REVIEW"  # 70 - 84: Ambiguous Variant / Review Queue
    NO_MATCH = "NO_MATCH"  # < 70: Distinct Product / Separate Cluster


class ConfidenceScoreBreakdown(BaseModel):
    """Detailed point contribution across all evaluated product identity dimensions."""

    model_config = ConfigDict(frozen=True)

    brand_points: int = Field(default=0, ge=0, le=20)
    model_points: int = Field(default=0, ge=0, le=25)
    ram_points: int = Field(default=0, ge=0, le=15)
    storage_points: int = Field(default=0, ge=0, le=15)
    chip_points: int = Field(default=0, ge=0, le=10)
    identifier_points: int = Field(default=0, ge=0, le=10)
    spec_points: int = Field(default=0, ge=0, le=5)

    @property
    def total_score(self) -> int:
        return (
            self.brand_points
            + self.model_points
            + self.ram_points
            + self.storage_points
            + self.chip_points
            + self.identifier_points
            + self.spec_points
        )


class IdentityConfidenceScorer:
    """Evaluates two product fingerprints against a standardized 100-point identity matrix."""

    @classmethod
    def calculate_confidence(
        cls,
        left: ProductFingerprint,
        right: ProductFingerprint,
    ) -> tuple[int, MatchConfidenceTier, ConfidenceScoreBreakdown]:
        """Compute the 100-point identity confidence score between two product fingerprints."""
        # Hard conflict rejection returns 0 immediately
        if left.category != right.category:
            return 0, MatchConfidenceTier.NO_MATCH, ConfidenceScoreBreakdown()

        # 1. Exact Global Identifier Match (GTIN / MPN)
        gtin_match = bool(left.gtin and right.gtin and left.gtin == right.gtin)
        mpn_match = bool(
            left.manufacturer_part_number
            and right.manufacturer_part_number
            and left.manufacturer_part_number.casefold()
            == right.manufacturer_part_number.casefold()
        )

        identifier_pts = 10 if (gtin_match or mpn_match) else 0

        # If exact GTIN or MPN matches and brands are identical, baseline is already 100
        if (gtin_match or mpn_match) and left.brand == right.brand:
            breakdown = ConfidenceScoreBreakdown(
                brand_points=20,
                model_points=25,
                ram_points=15,
                storage_points=15,
                chip_points=10,
                identifier_points=10,
                spec_points=5,
            )
            return 100, MatchConfidenceTier.MATCH, breakdown

        # 2. Brand Match (20 pts)
        brand_pts = 20 if left.brand == right.brand else 0

        # 3. Model / Family Match (25 pts)
        model_pts = 0
        if left.family and right.family and left.family == right.family:
            model_pts = 25
        elif left.model_name and right.model_name:
            clean_l = left.model_name.casefold().strip()
            clean_r = right.model_name.casefold().strip()
            if clean_l == clean_r:
                model_pts = 25
            elif clean_l in clean_r or clean_r in clean_l:
                model_pts = 18

        # 4. RAM Match (15 pts)
        ram_pts = 0
        left_ram = left.ram_gb if left.ram_gb is not None else left.attributes.get("ram_gb")
        right_ram = right.ram_gb if right.ram_gb is not None else right.attributes.get("ram_gb")
        if left_ram is not None and right_ram is not None:
            if left_ram == right_ram:
                ram_pts = 15
        elif left_ram is None and right_ram is None:
            ram_pts = 8

        # 5. Storage Match (15 pts)
        storage_pts = 0
        left_storage = (
            left.storage_gb if left.storage_gb is not None else left.attributes.get("storage_gb")
        )
        right_storage = (
            right.storage_gb if right.storage_gb is not None else right.attributes.get("storage_gb")
        )
        if left_storage is not None and right_storage is not None:
            if left_storage == right_storage:
                storage_pts = 15
        elif left_storage is None and right_storage is None:
            storage_pts = 8

        # 6. Chipset / CPU Match (10 pts)
        chip_pts = 0
        left_chip = left.chip or str(
            left.attributes.get("cpu_model") or left.attributes.get("chipset") or ""
        )
        right_chip = right.chip or str(
            right.attributes.get("cpu_model") or right.attributes.get("chipset") or ""
        )
        if left_chip and right_chip:
            if left_chip.casefold().strip() == right_chip.casefold().strip():
                chip_pts = 10
            elif (
                left_chip.casefold() in right_chip.casefold()
                or right_chip.casefold() in left_chip.casefold()
            ):
                chip_pts = 7
        elif not left_chip and not right_chip:
            chip_pts = 5

        # 7. Secondary Spec Match (Display / GPU / Color) (5 pts)
        spec_pts = 0
        if (
            left.attributes.get("color")
            and right.attributes.get("color")
            and str(left.attributes["color"]).casefold()
            == str(right.attributes["color"]).casefold()
        ):
            spec_pts += 3
        if left.gpu_model and right.gpu_model and left.gpu_model == right.gpu_model:
            spec_pts += 2

        breakdown = ConfidenceScoreBreakdown(
            brand_points=brand_pts,
            model_points=model_pts,
            ram_points=ram_pts,
            storage_points=storage_pts,
            chip_points=chip_pts,
            identifier_points=identifier_pts,
            spec_points=min(spec_pts, 5),
        )

        total = breakdown.total_score

        # Determine Tier (calibrated for scraper inputs where GTIN/MPN may be missing)
        if total >= 90:
            tier = MatchConfidenceTier.MATCH
        elif total >= 75:
            tier = MatchConfidenceTier.STRONG_MATCH
        elif total >= 60:
            tier = MatchConfidenceTier.REVIEW
        else:
            tier = MatchConfidenceTier.NO_MATCH

        return total, tier, breakdown
