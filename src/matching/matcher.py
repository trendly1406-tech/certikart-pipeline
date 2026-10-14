"""Category-aware product matching with multi-stage hard conflict elimination.

Provides confidence scoring and deterministic conflict rules.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz.fuzz import token_set_ratio

from categories import get_category
from matching.confidence import (
    ConfidenceScoreBreakdown,
    IdentityConfidenceScorer,
    MatchConfidenceTier,
)
from matching.fingerprint import ProductFingerprint
from matching.rules import check_hard_conflicts


class MatchDecision(StrEnum):
    MATCH = "match"
    REVIEW = "review"
    NO_MATCH = "no_match"


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: MatchDecision
    confidence: float = Field(ge=0, le=100)
    reasons: tuple[str, ...]
    score_breakdown: ConfidenceScoreBreakdown | None = None


def compare_products(left: ProductFingerprint, right: ProductFingerprint) -> MatchResult:
    """Compare two product fingerprints using strict rules and 100-pt confidence matrix."""
    # 1. Exact GTIN Match
    if left.gtin and right.gtin:
        same = left.gtin == right.gtin
        _, _, breakdown = IdentityConfidenceScorer.calculate_confidence(left, right)
        return MatchResult(
            decision=MatchDecision.MATCH if same else MatchDecision.NO_MATCH,
            confidence=100.0,
            reasons=("exact GTIN",) if same else ("different GTINs",),
            score_breakdown=breakdown,
        )

    # 2. Exact Manufacturer Part Number Match
    if left.manufacturer_part_number and right.manufacturer_part_number:
        same = left.manufacturer_part_number == right.manufacturer_part_number
        _, _, breakdown = IdentityConfidenceScorer.calculate_confidence(left, right)
        return MatchResult(
            decision=MatchDecision.MATCH if same else MatchDecision.NO_MATCH,
            confidence=100.0,
            reasons=("exact manufacturer part number",)
            if same
            else ("different manufacturer part numbers",),
            score_breakdown=breakdown,
        )

    # 3. Hard Conflict Elimination
    has_conflict, conflict_reason = check_hard_conflicts(left, right)
    if has_conflict and conflict_reason:
        return MatchResult(
            decision=MatchDecision.NO_MATCH,
            confidence=100.0,
            reasons=(conflict_reason,),
            score_breakdown=ConfidenceScoreBreakdown(),
        )

    # 4. Check for Missing Category Identity Attributes
    category = get_category(left.category)
    missing_attributes: list[str] = []
    for attr in category.identity_attributes:
        if left.value_for(attr) is None or right.value_for(attr) is None:
            missing_attributes.append(attr)

    _score_100, tier, breakdown = IdentityConfidenceScorer.calculate_confidence(left, right)

    if missing_attributes:
        similarity = float(token_set_ratio(left.model_name, right.model_name))
        return MatchResult(
            decision=MatchDecision.REVIEW,
            confidence=min(similarity, 85.0),
            reasons=(f"missing identity attributes: {', '.join(missing_attributes)}",),
            score_breakdown=breakdown,
        )

    # 5. Structured Multi-Attribute Scoring
    # Family Score (Weight: 30)
    family_score = 0.85
    if left.family and right.family:
        family_score = 1.0 if left.family == right.family else 0.0
    elif left.family or right.family:
        family_score = 0.80

    # Chip Score (Weight: 25)
    chip_score = 0.85
    if left.chip and right.chip:
        chip_score = 1.0 if left.chip == right.chip else 0.85
    elif left.chip or right.chip:
        chip_score = 0.80

    # RAM & Storage Score (Weight: 25)
    spec_score = 1.0

    # Model Name Token Set Similarity (Weight: 20)
    token_sim = float(token_set_ratio(left.model_name, right.model_name))

    # Composite Confidence (0 - 100)
    composite_confidence = (
        (family_score * 30.0)
        + (chip_score * 25.0)
        + (spec_score * 25.0)
        + ((token_sim / 100.0) * 20.0)
    )

    if (
        composite_confidence >= 88.0
        and token_sim >= 75.0
        and tier in (MatchConfidenceTier.MATCH, MatchConfidenceTier.STRONG_MATCH)
    ):
        return MatchResult(
            decision=MatchDecision.MATCH,
            confidence=round(composite_confidence, 2),
            reasons=("canonical product identity attributes match",),
            score_breakdown=breakdown,
        )
    elif composite_confidence >= 65.0 or tier == MatchConfidenceTier.REVIEW:
        return MatchResult(
            decision=MatchDecision.REVIEW,
            confidence=round(composite_confidence, 2),
            reasons=("same category identity attributes; model name evaluated",),
            score_breakdown=breakdown,
        )
    else:
        return MatchResult(
            decision=MatchDecision.NO_MATCH,
            confidence=round(composite_confidence, 2),
            reasons=("insufficient attribute similarity",),
            score_breakdown=breakdown,
        )
