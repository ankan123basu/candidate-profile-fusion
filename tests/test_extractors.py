"""Tests for data extractors."""

import json
import csv
import os
import tempfile
from pathlib import Path

import pytest

from src.extract.csv_extractor import CsvExtractor
from src.extract.ats_json import AtsJsonExtractor
from src.extract.notes import NotesExtractor


class TestCsvExtractor:
    """Tests for CSV extractor."""

    def _write_csv(self, tmp_path: Path, rows: list[dict], fieldnames: list[str]) -> Path:
        csv_file = tmp_path / "test.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return csv_file

    def test_basic_extraction(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            {"name": "Alice Chen", "email": "alice@test.com", "phone": "+14155550101",
             "current_company": "Acme", "title": "Engineer"},
        ], ["name", "email", "phone", "current_company", "title"])

        extractor = CsvExtractor()
        results = extractor.extract(csv_file)
        assert len(results) == 1
        key = list(results.keys())[0]
        assert key == "alice@test.com"
        fields = results[key]
        field_names = [f.field for f in fields]
        assert "full_name" in field_names
        assert "email" in field_names

    def test_empty_rows_skipped(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            {"name": "Alice", "email": "alice@test.com"},
            {"name": "", "email": ""},  # empty row
            {"name": "Bob", "email": "bob@test.com"},
        ], ["name", "email"])

        extractor = CsvExtractor()
        results = extractor.extract(csv_file)
        assert len(results) == 2

    def test_missing_file(self, tmp_path):
        extractor = CsvExtractor()
        results = extractor.extract(tmp_path / "nonexistent.csv")
        assert results == {}

    def test_skills_parsing(self, tmp_path):
        csv_file = self._write_csv(tmp_path, [
            {"name": "Alice", "email": "a@b.com", "skills": "Python;Java;React"},
        ], ["name", "email", "skills"])

        extractor = CsvExtractor()
        results = extractor.extract(csv_file)
        key = list(results.keys())[0]
        skill_field = [f for f in results[key] if f.field == "skills"][0]
        assert isinstance(skill_field.value, list)
        assert len(skill_field.value) == 3


class TestAtsJsonExtractor:
    """Tests for ATS JSON extractor."""

    def test_basic_extraction(self, tmp_path):
        data = {
            "candidates": [
                {
                    "full_name": "Alice Chen",
                    "email": "alice@test.com",
                    "company": "Acme",
                    "skills": ["Python", "Java"],
                }
            ]
        }
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(data))

        extractor = AtsJsonExtractor()
        results = extractor.extract(json_file)
        assert len(results) == 1

    def test_malformed_json(self, tmp_path):
        """Malformed JSON should not crash, just return empty."""
        json_file = tmp_path / "bad.json"
        json_file.write_text("{this is not valid json")

        extractor = AtsJsonExtractor()
        results = extractor.extract(json_file)
        assert results == {}

    def test_first_last_name_combination(self, tmp_path):
        data = [{"first_name": "Alice", "last_name": "Chen", "email": "a@b.com"}]
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(data))

        extractor = AtsJsonExtractor()
        results = extractor.extract(json_file)
        key = list(results.keys())[0]
        name_field = [f for f in results[key] if f.field == "full_name"]
        assert len(name_field) == 1
        assert name_field[0].value == "Alice Chen"

    def test_garbage_record_skipped(self, tmp_path):
        """Records with no mappable fields should produce no results."""
        data = [{"random_field": "value", "another_nonsense": 123}]
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(data))

        extractor = AtsJsonExtractor()
        results = extractor.extract(json_file)
        assert len(results) == 0

    def test_missing_file(self, tmp_path):
        extractor = AtsJsonExtractor()
        results = extractor.extract(tmp_path / "nonexistent.json")
        assert results == {}


class TestNotesExtractor:
    """Tests for recruiter notes extractor."""

    def test_basic_extraction(self, tmp_path):
        notes_file = tmp_path / "notes.txt"
        notes_file.write_text(
            "Name: Alice Chen\n"
            "Email: alice@test.com\n"
            "Company: Acme Corp\n"
            "Skills: Python, Java, React\n"
        )

        extractor = NotesExtractor()
        results = extractor.extract(notes_file)
        assert len(results) == 1

    def test_multiple_candidates(self, tmp_path):
        notes_file = tmp_path / "notes.txt"
        notes_file.write_text(
            "Name: Alice Chen\nEmail: alice@test.com\n"
            "---\n"
            "Name: Bob Smith\nEmail: bob@test.com\n"
        )

        extractor = NotesExtractor()
        results = extractor.extract(notes_file)
        assert len(results) == 2

    def test_missing_file(self, tmp_path):
        extractor = NotesExtractor()
        results = extractor.extract(tmp_path / "nonexistent.txt")
        assert results == {}

    def test_empty_file(self, tmp_path):
        notes_file = tmp_path / "empty.txt"
        notes_file.write_text("")

        extractor = NotesExtractor()
        results = extractor.extract(notes_file)
        assert results == {}
