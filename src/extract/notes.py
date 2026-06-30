"""
Recruiter notes extractor — parses free-text recruiter notes.

Notes are typically unstructured text with some semi-structured
patterns like "Company: Acme Corp" or "Skills: Python, Java".
Uses a mix of regex and heuristic extraction.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List

from src.schema import RawField, ExtractionMethod
from src.extract.base import BaseExtractor

logger = logging.getLogger(__name__)

# Patterns for semi-structured fields in notes
# "Key: Value" or "Key - Value" patterns
KV_PATTERN = re.compile(
    r'^(?P<key>[A-Za-z _]+?)\s*[:–—-]\s*(?P<value>.+)$',
    re.MULTILINE,
)

# Map common note keys → canonical field names
NOTES_KEY_MAP: dict[str, str] = {
    "name": "full_name",
    "candidate": "full_name",
    "candidate name": "full_name",
    "email": "email",
    "phone": "phone",
    "mobile": "phone",
    "contact": "phone",
    "company": "current_company",
    "current company": "current_company",
    "employer": "current_company",
    "title": "current_title",
    "position": "current_title",
    "role": "current_title",
    "current title": "current_title",
    "current role": "current_title",
    "location": "location",
    "city": "location",
    "based in": "location",
    "country": "country",
    "skills": "skills",
    "tech stack": "skills",
    "technologies": "skills",
    "experience": "years_of_experience",
    "years of experience": "years_of_experience",
    "yoe": "years_of_experience",
    "education": "education",
    "degree": "education",
    "certifications": "certifications",
    "certs": "certifications",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "notes": "summary",
    "summary": "summary",
    "comments": "summary",
    "impression": "summary",
    "feedback": "summary",
}

# Pattern to detect candidate blocks separated by delimiters
CANDIDATE_SEPARATOR = re.compile(
    r'^(?:---+|===+|\*\*\*+|#{2,})\s*$',
    re.MULTILINE,
)

EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
EXPERIENCE_PATTERN = re.compile(
    r'(\d{1,2})\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)?',
    re.IGNORECASE,
)


class NotesExtractor(BaseExtractor):
    """Extract candidate data from recruiter notes text files."""

    source_name = "notes"

    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        source_path = Path(source_path)
        results: Dict[str, List[RawField]] = {}

        if not source_path.exists():
            logger.error("Notes file not found: %s", source_path)
            return results

        try:
            with open(source_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error("Error reading notes file %s: %s", source_path, e)
            return results

        if not content.strip():
            logger.warning("Empty notes file: %s", source_path)
            return results

        # Split into candidate blocks if separators exist
        blocks = CANDIDATE_SEPARATOR.split(content)
        blocks = [b.strip() for b in blocks if b.strip()]

        if not blocks:
            blocks = [content]

        for block_idx, block in enumerate(blocks):
            fields = self._extract_from_block(block, block_idx)
            if fields:
                # Use email as candidate key if found
                candidate_key = None
                for f in fields:
                    if f.field == "email" and f.value:
                        candidate_key = str(f.value).lower().strip()
                        break
                if candidate_key is None:
                    candidate_key = f"notes_block_{block_idx}"

                results[candidate_key] = fields

        logger.info("Notes extractor: extracted %d candidates from %s", len(results), source_path)
        return results

    def _extract_from_block(self, block: str, block_idx: int) -> List[RawField]:
        """Extract fields from a single candidate block in notes."""
        fields: List[RawField] = []
        source_id = f"block_{block_idx}"
        found_fields: set[str] = set()

        # --- Key-Value pairs ---
        for match in KV_PATTERN.finditer(block):
            key = match.group("key").strip().lower()
            value = match.group("value").strip()

            canonical = NOTES_KEY_MAP.get(key)
            if canonical is None or not value:
                continue

            # Handle list fields
            if canonical in ("skills", "certifications"):
                sep = ";" if ";" in value else ","
                value = [s.strip() for s in value.split(sep) if s.strip()]

            # Handle numeric fields
            if canonical == "years_of_experience":
                try:
                    value = float(re.sub(r'[^\d.]', '', value))
                except (ValueError, TypeError):
                    continue

            fields.append(RawField(
                field=canonical,
                value=value,
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.REGEX,
            ))
            found_fields.add(canonical)

        # --- Fallback: scan for email if not already found ---
        if "email" not in found_fields:
            emails = EMAIL_PATTERN.findall(block)
            if emails:
                fields.append(RawField(
                    field="email",
                    value=emails[0],
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.REGEX,
                ))

        # --- Fallback: scan for experience years if not found ---
        if "years_of_experience" not in found_fields:
            exp_match = EXPERIENCE_PATTERN.search(block)
            if exp_match:
                fields.append(RawField(
                    field="years_of_experience",
                    value=float(exp_match.group(1)),
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.REGEX,
                ))

        # --- Capture any remaining text as summary (if substantial) ---
        if "summary" not in found_fields:
            # Remove matched KV lines, keep the rest as summary
            remaining_lines = []
            for line in block.split("\n"):
                line_stripped = line.strip()
                if line_stripped and not KV_PATTERN.match(line_stripped):
                    remaining_lines.append(line_stripped)
            remaining = " ".join(remaining_lines).strip()
            if len(remaining) > 20:  # only if there's substantial text
                fields.append(RawField(
                    field="summary",
                    value=remaining,
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.HEURISTIC,
                ))

        return fields
