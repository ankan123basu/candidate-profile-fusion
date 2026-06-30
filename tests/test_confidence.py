"""Tests for confidence scoring."""

from datetime import datetime

import pytest

from src.schema import (
    RawField,
    CandidateProfile,
    Provenance,
    FieldConfidence,
    ExtractionMethod,
    SkillEntry,
)
from src.confidence import (
    score_field,
    compute_confidence,
    SOURCE_WEIGHTS,
    METHOD_MODIFIERS,
)


def _make_profile_with_provenance(
    field_name: str,
    value,
    sources: list[tuple[str, ExtractionMethod]],
    has_conflict: bool = False,
) -> CandidateProfile:
    """Create a profile with specific provenance for testing."""
    provenance = [
        Provenance(
            field=field_name,
            source=src,
            method=method,
            raw_value=value,
        )
        for src, method in sources
    ]
    kwargs = {field_name: value, "provenance": provenance}
    profile = CandidateProfile(**kwargs)
    profile._conflicts = {field_name: has_conflict}
    return profile


class TestScoreField:
    """Tests for per-field confidence scoring."""

    def test_single_structured_csv_source(self):
        profile = _make_profile_with_provenance(
            "emails", ["alice@test.com"],
            [("csv", ExtractionMethod.STRUCTURED_PARSE)],
        )
        fc = score_field("emails", profile)
        assert fc is not None
        # Expected: 0.80 * 1.0 = 0.80
        assert fc.score == pytest.approx(0.80, abs=0.01)
        assert fc.source_count == 1
        assert fc.has_conflict is False

    def test_corroboration_bonus(self):
        profile = _make_profile_with_provenance(
            "emails", ["alice@test.com"],
            [
                ("csv", ExtractionMethod.STRUCTURED_PARSE),
                ("ats_json", ExtractionMethod.STRUCTURED_PARSE),
            ],
        )
        fc = score_field("emails", profile)
        assert fc is not None
        # Expected: 0.80 + 0.10 (1 extra source) = 0.90
        assert fc.score > 0.80
        assert fc.source_count == 2

    def test_conflict_penalty(self):
        profile = _make_profile_with_provenance(
            "headline", "Sr. Engineer",
            [
                ("csv", ExtractionMethod.STRUCTURED_PARSE),
                ("notes", ExtractionMethod.REGEX),
            ],
            has_conflict=True,
        )
        fc = score_field("headline", profile)
        assert fc is not None
        assert fc.has_conflict is True
        # Should be lower due to conflict penalty
        base = SOURCE_WEIGHTS["csv"] * METHOD_MODIFIERS[ExtractionMethod.STRUCTURED_PARSE]
        assert fc.score < base

    def test_heuristic_method_lower(self):
        profile = _make_profile_with_provenance(
            "full_name", "Alice Chen",
            [("resume", ExtractionMethod.HEURISTIC)],
        )
        fc = score_field("full_name", profile)
        assert fc is not None
        # resume(0.60) * heuristic(0.60) = 0.36
        assert fc.score < 0.50

    def test_null_field_returns_none(self):
        profile = CandidateProfile()  # all fields null/empty
        fc = score_field("full_name", profile)
        assert fc is None

    def test_empty_list_field_returns_none(self):
        profile = CandidateProfile()
        fc = score_field("emails", profile)
        assert fc is None


class TestComputeConfidence:
    """Tests for overall confidence computation."""

    def test_overall_confidence_range(self):
        profile = _make_profile_with_provenance(
            "emails", ["alice@test.com"],
            [("csv", ExtractionMethod.STRUCTURED_PARSE)],
        )
        profile.full_name = "Alice Chen"
        profile.provenance.append(
            Provenance(field="full_name", source="csv",
                       method=ExtractionMethod.STRUCTURED_PARSE, raw_value="Alice Chen")
        )
        result = compute_confidence(profile)
        assert 0.0 <= result.overall_confidence <= 1.0

    def test_more_fields_from_good_sources_higher_confidence(self):
        """Profile with many fields from reliable sources should score high."""
        profile = CandidateProfile(
            full_name="Alice Chen",
            emails=["alice@test.com"],
            phones=["+14155550101"],
            headline="Sr. Engineer",
        )
        profile.provenance = [
            Provenance(field=f, source="csv",
                       method=ExtractionMethod.STRUCTURED_PARSE, raw_value="test")
            for f in ["full_name", "emails", "phones", "headline"]
        ]
        profile._conflicts = {}
        result = compute_confidence(profile)
        assert result.overall_confidence > 0.5
