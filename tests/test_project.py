"""Tests for the projection engine."""

import pytest

from src.schema import (
    CandidateProfile, Provenance, FieldConfidence, ExtractionMethod,
    LocationData, LinksData, SkillEntry, ExperienceEntry, EducationEntry,
)
from src.project import project_profile, project_all, ProjectionError


def _make_test_profile() -> CandidateProfile:
    """Create a test profile with sample data."""
    return CandidateProfile(
        full_name="Alice Chen",
        emails=["alice@test.com"],
        phones=["+14155550101"],
        location=LocationData(city="San Francisco", region="CA", country="US"),
        links=LinksData(linkedin="https://linkedin.com/in/alice", github="https://github.com/alice"),
        headline="Senior Engineer",
        years_experience=8.0,
        skills=[
            SkillEntry(name="Python", confidence=0.9, sources=["csv"]),
            SkillEntry(name="Java", confidence=0.8, sources=["ats_json"]),
        ],
        experience=[ExperienceEntry(company="Acme Corp", title="Senior Engineer")],
        education=[EducationEntry(institution="Stanford", degree="M.S. Computer Science")],
        overall_confidence=0.85,
        field_confidences=[
            FieldConfidence(field="emails", score=0.9, source_count=2),
            FieldConfidence(field="full_name", score=0.85, source_count=1),
        ],
        provenance=[
            Provenance(field="emails", source="csv",
                       method=ExtractionMethod.STRUCTURED_PARSE, raw_value="alice@test.com"),
        ],
    )


class TestProjectProfile:
    """Tests for profile projection."""

    def test_basic_projection(self):
        config = {
            "fields": [
                {"name": "full_name", "from": "full_name", "type": "string"},
                {"name": "emails", "from": "emails", "type": "array"},
            ],
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert result["full_name"] == "Alice Chen"
        assert result["emails"] == ["alice@test.com"]
        assert len(result) == 2  # only requested fields

    def test_field_remapping_with_index(self):
        """'from' path with array index should extract single element."""
        config = {
            "fields": [
                {"name": "primary_email", "from": "emails[0]", "type": "string"},
            ],
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert result["primary_email"] == "alice@test.com"

    def test_field_remapping_with_list_map(self):
        """'from' path with skills[].name should map to flat list."""
        config = {
            "fields": [
                {"name": "skill_names", "from": "skills[].name", "type": "string[]"},
            ],
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert "Python" in result["skill_names"]
        assert "Java" in result["skill_names"]

    def test_nested_object_projection(self):
        """Location should be serialized as a dict."""
        config = {
            "fields": [
                {"name": "location", "from": "location", "type": "object"},
            ],
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert result["location"]["city"] == "San Francisco"
        assert result["location"]["country"] == "US"

    def test_on_missing_null(self):
        config = {
            "fields": [
                {"name": "nonexistent", "from": "nonexistent_field", "type": "string"},
            ],
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert "nonexistent" in result
        assert result["nonexistent"] is None

    def test_on_missing_omit(self):
        config = {
            "fields": [
                {"name": "nonexistent", "from": "nonexistent_field", "type": "string"},
            ],
            "on_missing": "omit",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert "nonexistent" not in result

    def test_on_missing_error(self):
        config = {
            "fields": [
                {"name": "nonexistent", "from": "nonexistent_field", "type": "string"},
            ],
            "on_missing": "error",
        }
        profile = _make_test_profile()
        with pytest.raises(ProjectionError):
            project_profile(profile, config)

    def test_include_confidence(self):
        config = {
            "fields": [
                {"name": "full_name", "from": "full_name", "type": "string"},
            ],
            "include_confidence": True,
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert "_confidence" in result
        assert result["_confidence"]["overall"] == 0.85

    def test_include_provenance(self):
        config = {
            "fields": [
                {"name": "full_name", "from": "full_name", "type": "string"},
            ],
            "include_provenance": True,
            "on_missing": "null",
        }
        profile = _make_test_profile()
        result = project_profile(profile, config)
        assert "_provenance" in result
        assert len(result["_provenance"]) > 0


class TestProjectAll:
    """Tests for batch projection."""

    def test_project_multiple(self):
        config = {
            "fields": [
                {"name": "full_name", "from": "full_name", "type": "string"},
            ],
            "on_missing": "null",
        }
        profiles = [_make_test_profile(), _make_test_profile()]
        results = project_all(profiles, config)
        assert len(results) == 2

    def test_failed_projection_skipped(self):
        """Profiles that fail on_missing='error' should be skipped."""
        config = {
            "fields": [
                {"name": "nonexistent", "from": "nonexistent_field", "type": "string"},
            ],
            "on_missing": "error",
        }
        profiles = [_make_test_profile()]
        results = project_all(profiles, config)
        assert len(results) == 0
