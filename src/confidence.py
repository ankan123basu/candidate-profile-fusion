"""
Confidence scoring engine.

Computes per-field and overall confidence scores for merged candidate profiles.

Formula:
  field_confidence = base_weight(source) x method_modifier x corroboration_bonus x conflict_penalty

  Where:
    - base_weight: reliability tier of the source that provided the winning value
    - method_modifier: how the value was extracted (structured > regex > heuristic)
    - corroboration_bonus: multiple sources agreeing -> higher confidence
    - conflict_penalty: disagreement across sources -> lower confidence

  overall_confidence = weighted_average(field_confidences)
    weighted by field importance (identity fields weighted higher)
"""

from __future__ import annotations

import logging
from typing import Optional

from src.schema import (
    CandidateProfile,
    FieldConfidence,
    ExtractionMethod,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring parameters
# ---------------------------------------------------------------------------

# Base reliability weight per source (0-1)
SOURCE_WEIGHTS: dict[str, float] = {
    "csv": 0.80,
    "ats_json": 0.85,
    "github": 0.70,
    "linkedin": 0.90,
    "resume": 0.60,
    "notes": 0.40,
}

# Extraction method modifier (multiplied with base weight)
METHOD_MODIFIERS: dict[ExtractionMethod, float] = {
    ExtractionMethod.STRUCTURED_PARSE: 1.0,
    ExtractionMethod.API: 0.95,
    ExtractionMethod.REGEX: 0.80,
    ExtractionMethod.HEURISTIC: 0.60,
}

# Corroboration bonus per additional agreeing source
CORROBORATION_BONUS = 0.10

# Conflict penalty when sources disagree
CONFLICT_PENALTY = 0.15

# Field importance weights for overall score computation
FIELD_IMPORTANCE: dict[str, float] = {
    "full_name": 1.5,
    "emails": 2.0,
    "phones": 1.2,
    "headline": 1.0,
    "location": 0.8,
    "skills": 1.3,
    "years_experience": 0.8,
    "education": 0.7,
    "experience": 0.6,
    "links": 0.4,
}


# ---------------------------------------------------------------------------
# Per-field scoring
# ---------------------------------------------------------------------------

def score_field(
    field_name: str,
    profile: CandidateProfile,
) -> Optional[FieldConfidence]:
    """
    Compute confidence score for a single field on a profile.

    Looks at the provenance records for this field to determine:
      - Which source provided the winning value (-> base_weight)
      - How it was extracted (-> method_modifier)
      - How many sources corroborate (-> bonus)
      - Whether sources conflict (-> penalty)

    Returns:
        FieldConfidence or None if the field has no value.
    """
    # Check if the field has a value
    field_value = getattr(profile, field_name, None)
    if field_value is None:
        return None
    # For list fields, check if non-empty
    if isinstance(field_value, list) and len(field_value) == 0:
        return None

    # Get provenance records for this field
    field_provenance = [p for p in profile.provenance if p.field == field_name]

    if not field_provenance:
        # Value exists but no provenance — assign a default moderate score
        return FieldConfidence(
            field=field_name,
            score=0.50,
            source_count=0,
            has_conflict=False,
        )

    # Base weight from the first (highest priority) provenance entry
    primary = field_provenance[0]
    base_weight = SOURCE_WEIGHTS.get(primary.source, 0.50)
    method_mod = METHOD_MODIFIERS.get(primary.method, 0.70)

    # Start with base score
    score = base_weight * method_mod

    # Corroboration: count distinct sources that contributed
    source_count = len(set(p.source for p in field_provenance))
    if source_count > 1:
        bonus = min((source_count - 1) * CORROBORATION_BONUS, 0.30)  # cap bonus
        score += bonus

    # Conflict detection
    has_conflict = getattr(profile, '_conflicts', {}).get(field_name, False)
    if has_conflict:
        score -= CONFLICT_PENALTY

    # Clamp to [0, 1]
    score = max(0.0, min(1.0, score))

    return FieldConfidence(
        field=field_name,
        score=round(score, 3),
        source_count=source_count,
        has_conflict=has_conflict,
    )


# ---------------------------------------------------------------------------
# Overall scoring
# ---------------------------------------------------------------------------

def compute_confidence(profile: CandidateProfile) -> CandidateProfile:
    """
    Compute all field confidences and overall confidence for a profile.

    Updates the profile in-place and returns it.

    Overall confidence = weighted average of field scores, weighted by
    FIELD_IMPORTANCE. Only non-null fields contribute.
    """
    scoreable_fields = [
        "full_name", "emails", "phones", "headline",
        "location", "skills", "years_experience", "education",
        "experience", "links",
    ]

    field_confidences: list[FieldConfidence] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for field_name in scoreable_fields:
        fc = score_field(field_name, profile)
        if fc is not None:
            field_confidences.append(fc)
            importance = FIELD_IMPORTANCE.get(field_name, 0.5)
            weighted_sum += fc.score * importance
            weight_total += importance

    profile.field_confidences = field_confidences
    profile.overall_confidence = round(
        weighted_sum / weight_total if weight_total > 0 else 0.0,
        3,
    )

    logger.debug(
        "Confidence for %s: overall=%.3f (%d fields scored)",
        (profile.emails[0] if profile.emails else profile.full_name or "unknown"),
        profile.overall_confidence,
        len(field_confidences),
    )

    return profile
