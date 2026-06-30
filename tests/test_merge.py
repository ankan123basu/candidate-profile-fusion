"""Tests for the merge engine."""

from datetime import datetime

import pytest

from src.schema import RawField, ExtractionMethod, CandidateProfile, SkillEntry
from src.merge import (
    CandidateCluster,
    cluster_candidates,
    resolve_cluster,
    merge_all,
)


def _make_raw_field(field, value, source, source_id="test", timestamp=None):
    """Helper to create a RawField."""
    return RawField(
        field=field,
        value=value,
        source=source,
        source_id=source_id,
        extraction_method=ExtractionMethod.STRUCTURED_PARSE,
        timestamp=timestamp or datetime.utcnow(),
    )


class TestClustering:
    """Tests for candidate clustering."""

    def test_merge_by_email(self):
        """Records with same email should merge into one cluster."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
                _make_raw_field("full_name", "Alice Chen", "csv"),
            ],
            "ats_1": [
                _make_raw_field("email", "alice@test.com", "ats_json"),
                _make_raw_field("full_name", "Alice C.", "ats_json"),
            ],
        }
        clusters = cluster_candidates(records)
        assert len(clusters) == 1
        assert len(clusters[0].raw_fields) == 4

    def test_no_merge_different_emails(self):
        """Records with different emails stay separate."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
            ],
            "csv_2": [
                _make_raw_field("email", "bob@test.com", "csv"),
            ],
        }
        clusters = cluster_candidates(records)
        assert len(clusters) == 2

    def test_merge_by_fuzzy_name_company(self):
        """Records without email but matching name+company should merge."""
        records = {
            "csv_1": [
                _make_raw_field("full_name", "Alice Chen", "csv"),
                _make_raw_field("current_company", "Acme Corp", "csv"),
            ],
            "notes_1": [
                _make_raw_field("full_name", "Alice Chenn", "notes"),  # typo
                _make_raw_field("current_company", "Acme Corporation", "notes"),
            ],
        }
        clusters = cluster_candidates(records)
        # Should merge due to fuzzy match
        assert len(clusters) == 1


class TestFieldResolution:
    """Tests for field-level conflict resolution."""

    def test_csv_wins_over_notes_for_headline(self):
        """CSV has higher priority than notes for headline (current_title)."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
                _make_raw_field("current_title", "Sr. Engineer", "csv"),
            ],
            "notes_1": [
                _make_raw_field("email", "alice@test.com", "notes"),
                _make_raw_field("current_title", "Senior Engineer at Acme", "notes"),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        assert profiles[0].headline == "Sr. Engineer"

    def test_skills_union(self):
        """Skills from multiple sources should be merged (union)."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
                _make_raw_field("skills", ["Python", "Java"], "csv"),
            ],
            "ats_1": [
                _make_raw_field("email", "alice@test.com", "ats_json"),
                _make_raw_field("skills", ["Python", "React"], "ats_json"),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        skill_names = [s.name for s in profiles[0].skills]
        assert "Python" in skill_names
        assert "Java" in skill_names
        assert "React" in skill_names

    def test_provenance_tracked(self):
        """Provenance should track all contributing sources."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
                _make_raw_field("full_name", "Alice Chen", "csv"),
            ],
            "ats_1": [
                _make_raw_field("email", "alice@test.com", "ats_json"),
                _make_raw_field("full_name", "Alice C. Chen", "ats_json"),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        name_provenance = [p for p in profiles[0].provenance if p.field == "full_name"]
        assert len(name_provenance) == 2
        sources = {p.source for p in name_provenance}
        assert "csv" in sources
        assert "ats_json" in sources

    def test_emails_union(self):
        """Emails from multiple sources should be unioned and deduplicated."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
            ],
            "ats_1": [
                _make_raw_field("email", "alice@test.com", "ats_json"),
            ],
        }
        profiles = merge_all(records, apply_normalization=False)
        assert len(profiles) == 1
        assert "alice@test.com" in profiles[0].emails

    def test_location_parsed_into_nested(self):
        """Location string should be parsed into LocationData."""
        records = {
            "csv_1": [
                _make_raw_field("email", "alice@test.com", "csv"),
                _make_raw_field("location", "San Francisco, CA", "csv"),
                _make_raw_field("country", "US", "csv"),
            ],
        }
        profiles = merge_all(records, apply_normalization=True)
        assert profiles[0].location is not None
        assert profiles[0].location.city == "San Francisco"
        assert profiles[0].location.region == "CA"
        assert profiles[0].location.country == "US"
