"""
CSV extractor — parses recruiter CSV exports.

Expected columns (flexible — we map whatever we find):
  name, email, phone, current_company, title, location, country,
  skills, years_of_experience, education, linkedin_url, github_url

Handles: missing columns, empty rows, malformed data — skips and logs.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, List

from src.schema import RawField, ExtractionMethod
from src.extract.base import BaseExtractor

logger = logging.getLogger(__name__)

# Map CSV column headers (lowercased, stripped) → canonical field names
COLUMN_MAP: dict[str, str] = {
    "name": "full_name",
    "full_name": "full_name",
    "fullname": "full_name",
    "candidate_name": "full_name",
    "email": "email",
    "email_address": "email",
    "phone": "phone",
    "phone_number": "phone",
    "mobile": "phone",
    "current_company": "current_company",
    "company": "current_company",
    "employer": "current_company",
    "title": "current_title",
    "current_title": "current_title",
    "job_title": "current_title",
    "position": "current_title",
    "location": "location",
    "city": "location",
    "country": "country",
    "skills": "skills",
    "years_of_experience": "years_of_experience",
    "experience": "years_of_experience",
    "yoe": "years_of_experience",
    "education": "education",
    "degree": "education",
    "certifications": "certifications",
    "linkedin_url": "linkedin_url",
    "linkedin": "linkedin_url",
    "github_url": "github_url",
    "github": "github_url",
}


class CsvExtractor(BaseExtractor):
    """Extract candidate data from a recruiter CSV file."""

    source_name = "csv"

    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        source_path = Path(source_path)
        results: Dict[str, List[RawField]] = {}

        try:
            with open(source_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    logger.warning("CSV file %s has no header row", source_path)
                    return results

                # Build column mapping for this specific file
                col_to_field: dict[str, str] = {}
                for col in reader.fieldnames:
                    key = col.strip().lower().replace(" ", "_")
                    if key in COLUMN_MAP:
                        col_to_field[col] = COLUMN_MAP[key]
                    else:
                        logger.debug("Unmapped CSV column: %s", col)

                for row_idx, row in enumerate(reader, start=1):
                    source_id = f"row_{row_idx}"
                    fields: List[RawField] = []

                    for csv_col, canonical_field in col_to_field.items():
                        raw_value = self._safe_strip(row.get(csv_col))
                        if raw_value is None:
                            continue

                        # Skills come as semicolon or comma separated
                        if canonical_field == "skills":
                            # Try semicolon first, then comma
                            sep = ";" if ";" in raw_value else ","
                            value = [s.strip() for s in raw_value.split(sep) if s.strip()]
                        elif canonical_field == "years_of_experience":
                            try:
                                value = float(raw_value)
                            except ValueError:
                                logger.warning(
                                    "Row %d: non-numeric years_of_experience '%s'",
                                    row_idx, raw_value,
                                )
                                continue
                        elif canonical_field == "certifications":
                            sep = ";" if ";" in raw_value else ","
                            value = [s.strip() for s in raw_value.split(sep) if s.strip()]
                        else:
                            value = raw_value

                        fields.append(RawField(
                            field=canonical_field,
                            value=value,
                            source=self.source_name,
                            source_id=source_id,
                            extraction_method=ExtractionMethod.STRUCTURED_PARSE,
                        ))

                    if fields:
                        # Use email as candidate key if available, else row id
                        candidate_key = None
                        for f in fields:
                            if f.field == "email" and f.value:
                                candidate_key = str(f.value).lower().strip()
                                break
                        if candidate_key is None:
                            candidate_key = f"csv_{source_id}"

                        results[candidate_key] = fields
                    else:
                        logger.debug("Row %d: no usable fields, skipping", row_idx)

        except FileNotFoundError:
            logger.error("CSV file not found: %s", source_path)
        except Exception as e:
            logger.error("Error parsing CSV %s: %s", source_path, e)

        logger.info("CSV extractor: extracted %d candidates from %s", len(results), source_path)
        return results
