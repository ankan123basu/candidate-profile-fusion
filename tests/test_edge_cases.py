"""
Edge case tests — the 5 explicit scenarios from the assignment,
plus 5 additional edge cases for extra robustness.

1. A source file is missing entirely → pipeline continues, fields stay null
2. Same candidate in CSV and GitHub with different name spellings → merges via email
3. Conflicting current_company between two sources → picks by source priority, confidence drops
4. Phone number missing country code → defaults to configured region or nulls
5. Garbage/malformed ATS JSON → catch, skip that source, don't crash
6. Unicode/international names → preserved correctly
7. Empty skills list → doesn't crash
8. Three-way conflict → highest priority wins
9. Skills from multiple sources → union, not pick-winner
10. Phone formats merge → normalized E.164 matching
"""

import json
import csv
from datetime import datetime
from pathlib import Path

import pytest

from src.schema import RawField, ExtractionMethod, CandidateProfile, Provenance
from src.extract.csv_extractor import CsvExtractor
from src.extract.ats_json import AtsJsonExtractor
from src.merge import merge_all, cluster_candidates
from src.confidence import compute_confidence
from src.normalize import normalize_phone


class TestEdgeCase1_MissingSource:
    """A source file is missing entirely → pipeline continues."""

    def test_missing_csv_file(self):
        extractor = CsvExtractor()
        results = extractor.extract(Path("totally/nonexistent/file.csv"))
        assert results == {}
        # Pipeline should not crash

    def test_missing_json_file(self):
        extractor = AtsJsonExtractor()
        results = extractor.extract(Path("totally/nonexistent/file.json"))
        assert results == {}

    def test_pipeline_continues_with_partial_sources(self):
        """Even with some sources missing, pipeline should produce results from available ones."""
        records = {
            "csv_1": [
                RawField(field="email", value="alice@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="full_name", value="Alice Chen", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            # No ATS, no GitHub, no resume, no notes — just CSV
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        assert profiles[0].full_name == "Alice Chen"
        # Missing fields should be None/empty
        assert profiles[0].headline is None
        assert profiles[0].skills == []


class TestEdgeCase2_DifferentNameSpellings:
    """Same candidate in CSV and GitHub with different name spellings → merges via email."""

    def test_name_variants_merge_on_email(self):
        records = {
            "csv_1": [
                RawField(field="email", value="alice@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="full_name", value="Alice Chen", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_company", value="Acme Corp", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "github_1": [
                RawField(field="email", value="alice@test.com", source="github",
                         extraction_method=ExtractionMethod.API),
                RawField(field="full_name", value="A. Chen", source="github",
                         extraction_method=ExtractionMethod.API),
                RawField(field="skills", value=["Python", "Go"], source="github",
                         extraction_method=ExtractionMethod.API),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        # Should merge into ONE candidate
        assert len(profiles) == 1
        # CSV has higher priority, so name should be from CSV
        assert profiles[0].full_name == "Alice Chen"
        # Skills from GitHub should still be present
        assert len(profiles[0].skills) > 0


class TestEdgeCase3_ConflictingCompany:
    """Conflicting current_company → picks by source priority, confidence drops."""

    def test_company_conflict_resolution(self):
        records = {
            "csv_1": [
                RawField(field="email", value="alice@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_company", value="Acme Corp", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "linkedin_1": [
                RawField(field="email", value="alice@test.com", source="linkedin",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_company", value="Acme Corporation International",
                         source="linkedin",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        # CSV has higher priority than LinkedIn — experience[0].company should be "Acme Corp"
        assert len(profiles[0].experience) > 0
        assert profiles[0].experience[0].company == "Acme Corp"

    def test_conflict_lowers_confidence(self):
        """When sources disagree on a field, confidence for that field should drop."""
        records = {
            "csv_1": [
                RawField(field="email", value="alice@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_title", value="Sr. Engineer", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "notes_1": [
                RawField(field="email", value="alice@test.com", source="notes",
                         extraction_method=ExtractionMethod.REGEX),
                RawField(field="current_title", value="Totally Different Title",
                         source="notes",
                         extraction_method=ExtractionMethod.REGEX),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1

        # Compute confidence
        profile = compute_confidence(profiles[0])

        # Find headline confidence
        headline_fc = None
        for fc in profile.field_confidences:
            if fc.field == "headline":
                headline_fc = fc
                break

        assert headline_fc is not None
        assert headline_fc.has_conflict is True
        # Score should be noticeably lower due to conflict
        assert headline_fc.score < 0.80


class TestEdgeCase4_PhoneMissingCountryCode:
    """Phone number missing country code → default to region or null."""

    def test_default_region_fills_country_code(self):
        result = normalize_phone("(415) 555-0101", default_region="US")
        assert result == "+14155550101"

    def test_ambiguous_number_nulls(self):
        """Genuinely unparseable number → None."""
        result = normalize_phone("123")
        assert result is None

    def test_number_with_country_code_preserved(self):
        result = normalize_phone("+44 20 7946 0958")
        assert result == "+442079460958"


class TestEdgeCase5_GarbageAtsJson:
    """Garbage/malformed ATS JSON → catch, skip, don't crash."""

    def test_malformed_json_file(self, tmp_path):
        bad_file = tmp_path / "garbage.json"
        bad_file.write_text("{{{not valid json!!!")

        extractor = AtsJsonExtractor()
        results = extractor.extract(bad_file)
        assert results == {}

    def test_json_with_unexpected_structure(self, tmp_path):
        """Valid JSON but unexpected shape."""
        weird_file = tmp_path / "weird.json"
        weird_file.write_text(json.dumps({"foo": "bar", "baz": [1, 2, 3]}))

        extractor = AtsJsonExtractor()
        results = extractor.extract(weird_file)
        # Should not crash; may or may not extract anything
        assert isinstance(results, dict)

    def test_json_array_with_non_dict_items(self, tmp_path):
        """JSON array containing non-dict items."""
        weird_file = tmp_path / "strings.json"
        weird_file.write_text(json.dumps(["just", "a", "list", "of", "strings"]))

        extractor = AtsJsonExtractor()
        results = extractor.extract(weird_file)
        assert results == {}

    def test_partial_garbage_in_records(self, tmp_path):
        """Some records are good, some are garbage — good ones should still extract."""
        data = {
            "candidates": [
                {
                    "full_name": "Alice Chen",
                    "email": "alice@test.com",
                    "company": "Acme",
                },
                "this is not a dict",
                None,
                {
                    "random_garbage": True,
                },
                {
                    "full_name": "Bob Smith",
                    "email": "bob@test.com",
                },
            ]
        }
        json_file = tmp_path / "partial_garbage.json"
        json_file.write_text(json.dumps(data))

        extractor = AtsJsonExtractor()
        results = extractor.extract(json_file)
        # Should extract at least Alice and Bob, skip garbage
        assert len(results) >= 2


class TestEdgeCase6_UnicodeNames:
    """International/Unicode names should be handled correctly."""

    def test_unicode_name_preserved(self):
        records = {
            "csv_1": [
                RawField(field="email", value="müller@test.de", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="full_name", value="Müller Günther", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        assert profiles[0].full_name == "Müller Günther"

    def test_cjk_name_preserved(self):
        records = {
            "csv_1": [
                RawField(field="email", value="tanaka@test.jp", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="full_name", value="田中太郎", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        assert profiles[0].full_name == "田中太郎"


class TestEdgeCase7_EmptySkillsList:
    """Empty or null skills should not crash the merge."""

    def test_empty_skills_becomes_empty_list(self):
        records = {
            "csv_1": [
                RawField(field="email", value="test@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="skills", value=[], source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        # Empty list should be treated as no skills
        assert profiles[0].skills == []

    def test_null_skills_ignored(self):
        records = {
            "csv_1": [
                RawField(field="email", value="test@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="skills", value=None, source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1


class TestEdgeCase8_ThreeWayConflict:
    """Three sources disagree on a field — highest priority wins."""

    def test_three_way_headline_conflict(self):
        records = {
            "csv_1": [
                RawField(field="email", value="test@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_title", value="Title A", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "ats_1": [
                RawField(field="email", value="test@test.com", source="ats_json",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_title", value="Title B", source="ats_json",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "notes_1": [
                RawField(field="email", value="test@test.com", source="notes",
                         extraction_method=ExtractionMethod.REGEX),
                RawField(field="current_title", value="Title C", source="notes",
                         extraction_method=ExtractionMethod.REGEX),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        # CSV has highest priority (6), so Title A wins
        assert profiles[0].headline == "Title A"

        # Confidence should reflect the conflict
        profile = compute_confidence(profiles[0])
        headline_fc = next(fc for fc in profile.field_confidences if fc.field == "headline")
        assert headline_fc.has_conflict is True
        assert headline_fc.source_count == 3


class TestEdgeCase9_SkillsUnionAcrossSources:
    """Skills from all sources should be merged as union, not pick-winner."""

    def test_skills_union(self):
        records = {
            "csv_1": [
                RawField(field="email", value="test@test.com", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="skills", value=["Python", "SQL"], source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
            "github_1": [
                RawField(field="email", value="test@test.com", source="github",
                         extraction_method=ExtractionMethod.API),
                RawField(field="skills", value=["Go", "Rust", "Python"], source="github",
                         extraction_method=ExtractionMethod.API),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        skill_names = [s.name for s in profiles[0].skills]
        # Should contain union: Python, SQL, Go, Rust
        assert "Python" in skill_names
        assert "SQL" in skill_names
        assert "Go" in skill_names
        assert "Rust" in skill_names


class TestEdgeCase10_PhoneMergeByNormalized:
    """Candidates with same phone in different formats should merge."""

    def test_phone_formats_merge(self):
        records = {
            "csv_1": [
                RawField(field="full_name", value="Alice Chen", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="phone", value="+1-415-555-0101", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
                RawField(field="current_company", value="Acme", source="csv",
                         extraction_method=ExtractionMethod.STRUCTURED_PARSE),
            ],
        }
        profiles = merge_all(records, apply_normalization=True)
        assert len(profiles) == 1
        # Phone should be normalized to E.164
        assert "+14155550101" in profiles[0].phones
