"""Tests for the validation layer."""

import pytest

from src.schema import CandidateProfile
from src.validate import (
    validate_canonical,
    validate_projected,
    generate_schema_from_config,
    validate_all_projected,
)


class TestValidateCanonical:
    """Tests for canonical profile validation."""

    def test_valid_profile(self):
        profile = CandidateProfile(
            full_name="Alice Chen",
            emails=["alice@test.com"],
        )
        result = validate_canonical(profile)
        assert result.is_valid

    def test_no_identity_fields(self):
        """Profile with no name/email/phone should fail."""
        profile = CandidateProfile(headline="Some headline")
        result = validate_canonical(profile)
        assert not result.is_valid
        assert any("identity" in e["field"] for e in result.errors)

    def test_invalid_email(self):
        profile = CandidateProfile(emails=["not-an-email"])
        result = validate_canonical(profile)
        assert not result.is_valid

    def test_negative_experience(self):
        profile = CandidateProfile(
            full_name="Test",
            years_experience=-5,
        )
        result = validate_canonical(profile)
        assert not result.is_valid


class TestValidateProjected:
    """Tests for projected output validation."""

    def test_valid_projected(self):
        config = {
            "fields": [
                {"name": "full_name", "type": "string"},
                {"name": "skills", "type": "array"},
            ],
            "on_missing": "null",
        }
        projected = {"full_name": "Alice Chen", "skills": ["Python"]}
        result = validate_projected(projected, config)
        assert result.is_valid

    def test_wrong_type(self):
        config = {
            "fields": [
                {"name": "full_name", "type": "string"},
            ],
            "on_missing": "null",
        }
        projected = {"full_name": 12345}  # should be string
        result = validate_projected(projected, config)
        assert not result.is_valid

    def test_extra_field_not_allowed(self):
        config = {
            "fields": [
                {"name": "full_name", "type": "string"},
            ],
            "on_missing": "null",
        }
        projected = {"full_name": "Alice", "extra_field": "surprise"}
        result = validate_projected(projected, config)
        assert not result.is_valid


class TestGenerateSchema:
    """Tests for dynamic schema generation."""

    def test_basic_schema(self):
        config = {
            "fields": [
                {"name": "full_name", "type": "string"},
                {"name": "years_experience", "type": "number"},
            ],
            "on_missing": "null",
        }
        schema = generate_schema_from_config(config)
        assert "properties" in schema
        assert "full_name" in schema["properties"]
        assert "years_experience" in schema["properties"]

    def test_required_fields_on_error_policy(self):
        config = {
            "fields": [
                {"name": "full_name", "type": "string"},
            ],
            "on_missing": "error",
        }
        schema = generate_schema_from_config(config)
        assert "required" in schema
        assert "full_name" in schema["required"]
