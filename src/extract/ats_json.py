"""
ATS JSON extractor — parses applicant tracking system JSON exports.

ATS systems emit nested JSON blobs with varying schemas. This extractor
handles common ATS export shapes and gracefully skips malformed records.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.schema import RawField, ExtractionMethod
from src.extract.base import BaseExtractor

logger = logging.getLogger(__name__)

# Map common ATS JSON keys (various nesting levels) → canonical field names
FIELD_MAP: dict[str, str] = {
    "name": "full_name",
    "full_name": "full_name",
    "candidate_name": "full_name",
    "first_name": "_first_name",   # special: combine later
    "last_name": "_last_name",     # special: combine later
    "email": "email",
    "email_address": "email",
    "phone": "phone",
    "phone_number": "phone",
    "mobile": "phone",
    "company": "current_company",
    "current_company": "current_company",
    "current_employer": "current_company",
    "title": "current_title",
    "current_title": "current_title",
    "job_title": "current_title",
    "position": "current_title",
    "location": "location",
    "city": "location",
    "address": "location",
    "country": "country",
    "skills": "skills",
    "technologies": "skills",
    "years_of_experience": "years_of_experience",
    "experience_years": "years_of_experience",
    "total_experience": "years_of_experience",
    "education": "education",
    "degree": "education",
    "certifications": "certifications",
    "certificates": "certifications",
    "linkedin_url": "linkedin_url",
    "linkedin": "linkedin_url",
    "linkedin_profile": "linkedin_url",
    "github_url": "github_url",
    "github": "github_url",
    "github_profile": "github_url",
    "summary": "summary",
    "bio": "summary",
    "notes": "summary",
    "last_updated": "last_updated",
    "updated_at": "last_updated",
    "modified_date": "last_updated",
}


def _flatten_record(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts into dot-path keys."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_record(value, full_key))
        else:
            flat[full_key] = value
            # Also keep the leaf key for simpler matching
            flat[key] = value
    return flat


class AtsJsonExtractor(BaseExtractor):
    """Extract candidate data from an ATS JSON export."""

    source_name = "ats_json"

    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        source_path = Path(source_path)
        results: Dict[str, List[RawField]] = {}

        try:
            with open(source_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.error("ATS JSON file not found: %s", source_path)
            return results
        except json.JSONDecodeError as e:
            logger.error("Malformed JSON in %s: %s", source_path, e)
            return results
        except Exception as e:
            logger.error("Error reading ATS JSON %s: %s", source_path, e)
            return results

        # Handle both single record and list of records
        records: list[dict] = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Check if it's a wrapper like {"candidates": [...]}
            for key in ("candidates", "records", "data", "results", "applicants"):
                if key in data and isinstance(data[key], list):
                    records = data[key]
                    break
            else:
                # Single record
                records = [data]

        for rec_idx, record in enumerate(records):
            if not isinstance(record, dict):
                logger.warning("Record %d is not a dict, skipping", rec_idx)
                continue

            try:
                fields = self._extract_record(record, rec_idx)
            except Exception as e:
                logger.warning("Error extracting record %d: %s", rec_idx, e)
                continue

            if fields:
                # Use email as candidate key if available
                candidate_key = None
                for f in fields:
                    if f.field == "email" and f.value:
                        candidate_key = str(f.value).lower().strip()
                        break
                if candidate_key is None:
                    candidate_key = f"ats_{rec_idx}"

                results[candidate_key] = fields

        logger.info("ATS JSON extractor: extracted %d candidates from %s", len(results), source_path)
        return results

    def _extract_record(self, record: dict[str, Any], rec_idx: int) -> List[RawField]:
        """Extract fields from a single ATS record."""
        flat = _flatten_record(record)
        fields: List[RawField] = []
        source_id = f"record_{rec_idx}"

        # Track first/last name for combining
        first_name = None
        last_name = None

        for raw_key, raw_value in flat.items():
            normalized_key = raw_key.strip().lower().replace(" ", "_")
            canonical = FIELD_MAP.get(normalized_key)

            if canonical is None:
                continue

            value = self._safe_strip(str(raw_value)) if not isinstance(raw_value, (list, dict)) else raw_value
            if value is None or value == "" or value == "None":
                continue

            # Handle special first/last name combination
            if canonical == "_first_name":
                first_name = value
                continue
            if canonical == "_last_name":
                last_name = value
                continue

            # Handle skills/certifications as lists
            if canonical in ("skills", "certifications"):
                if isinstance(value, str):
                    sep = ";" if ";" in value else ","
                    value = [s.strip() for s in value.split(sep) if s.strip()]
                elif not isinstance(value, list):
                    value = [str(value)]

            # Handle years_of_experience as float
            if canonical == "years_of_experience":
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    logger.debug("Record %d: non-numeric experience '%s'", rec_idx, value)
                    continue

            fields.append(RawField(
                field=canonical,
                value=value,
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.STRUCTURED_PARSE,
            ))

        # Combine first + last name if no full_name was found
        if first_name or last_name:
            has_full_name = any(f.field == "full_name" for f in fields)
            if not has_full_name:
                combined = " ".join(filter(None, [first_name, last_name]))
                if combined:
                    fields.append(RawField(
                        field="full_name",
                        value=combined,
                        source=self.source_name,
                        source_id=source_id,
                        extraction_method=ExtractionMethod.STRUCTURED_PARSE,
                    ))

        return fields
