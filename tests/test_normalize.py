"""Tests for normalizer functions."""

import pytest
from src.normalize import (
    normalize_phone,
    normalize_date,
    normalize_country,
    normalize_skill,
    normalize_skills,
    normalize_email,
    normalize_name,
)


class TestNormalizePhone:
    """Tests for phone → E.164 normalization."""

    def test_us_number_with_country_code(self):
        assert normalize_phone("+14155550101") == "+14155550101"

    def test_us_number_without_country_code(self):
        result = normalize_phone("(415) 555-0101", default_region="US")
        assert result == "+14155550101"

    def test_us_number_dashes(self):
        result = normalize_phone("415-555-0101", default_region="US")
        assert result == "+14155550101"

    def test_uk_number(self):
        result = normalize_phone("+44 20 7946 0958")
        assert result == "+442079460958"

    def test_invalid_number(self):
        assert normalize_phone("not-a-phone") is None

    def test_empty_string(self):
        assert normalize_phone("") is None

    def test_none(self):
        assert normalize_phone(None) is None

    def test_short_number(self):
        """Too short to be valid."""
        assert normalize_phone("123") is None


class TestNormalizeDate:
    """Tests for date → YYYY-MM normalization."""

    def test_iso_date(self):
        assert normalize_date("2024-01-15") == "2024-01"

    def test_month_year(self):
        assert normalize_date("Jan 2024") == "2024-01"

    def test_full_month_year(self):
        assert normalize_date("January 2024") == "2024-01"

    def test_slash_format(self):
        assert normalize_date("01/15/2024") == "2024-01"

    def test_year_only(self):
        result = normalize_date("2024")
        assert result is not None
        assert result.startswith("2024")

    def test_invalid_date(self):
        assert normalize_date("not-a-date") is None

    def test_empty(self):
        assert normalize_date("") is None

    def test_none(self):
        assert normalize_date(None) is None


class TestNormalizeCountry:
    """Tests for country → ISO 3166-1 alpha-2 normalization."""

    def test_alpha_2(self):
        assert normalize_country("US") == "US"

    def test_alpha_3(self):
        assert normalize_country("USA") == "US"

    def test_full_name(self):
        assert normalize_country("United States") == "US"

    def test_common_alias_uk(self):
        assert normalize_country("UK") == "GB"

    def test_full_name_uk(self):
        assert normalize_country("United Kingdom") == "GB"

    def test_india(self):
        assert normalize_country("India") == "IN"

    def test_case_insensitive(self):
        assert normalize_country("united states") == "US"

    def test_invalid(self):
        assert normalize_country("NotACountry12345") is None

    def test_empty(self):
        assert normalize_country("") is None

    def test_none(self):
        assert normalize_country(None) is None


class TestNormalizeSkill:
    """Tests for skill → canonical taxonomy name."""

    def test_exact_match(self):
        assert normalize_skill("python") == "Python"

    def test_alias(self):
        assert normalize_skill("js") == "JavaScript"

    def test_case_insensitive(self):
        assert normalize_skill("PYTHON") == "Python"

    def test_fuzzy_match(self):
        result = normalize_skill("pythoon")  # typo
        assert result == "Python"

    def test_no_match(self):
        assert normalize_skill("xyznonexistent") is None

    def test_empty(self):
        assert normalize_skill("") is None

    def test_none(self):
        assert normalize_skill(None) is None


class TestNormalizeSkills:
    """Tests for batch skill normalization."""

    def test_deduplicates(self):
        result = normalize_skills(["python", "Python", "py"])
        assert result == ["Python"]

    def test_multiple(self):
        result = normalize_skills(["python", "javascript", "react"])
        assert "Python" in result
        assert "JavaScript" in result
        assert "React" in result

    def test_empty_list(self):
        assert normalize_skills([]) == []

    def test_none(self):
        assert normalize_skills(None) == []


class TestNormalizeEmail:
    """Tests for email normalization."""

    def test_lowercase(self):
        assert normalize_email("Alice@Example.COM") == "alice@example.com"

    def test_strip_whitespace(self):
        assert normalize_email("  alice@example.com  ") == "alice@example.com"

    def test_invalid_no_at(self):
        assert normalize_email("not-an-email") is None

    def test_empty(self):
        assert normalize_email("") is None

    def test_none(self):
        assert normalize_email(None) is None


class TestNormalizeName:
    """Tests for name normalization."""

    def test_title_case(self):
        assert normalize_name("alice chen") == "Alice Chen"

    def test_collapse_spaces(self):
        assert normalize_name("alice   chen") == "Alice Chen"

    def test_strip(self):
        assert normalize_name("  Alice Chen  ") == "Alice Chen"

    def test_empty(self):
        assert normalize_name("") is None

    def test_none(self):
        assert normalize_name(None) is None
