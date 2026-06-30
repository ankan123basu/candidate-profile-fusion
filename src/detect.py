"""
Source type detector — auto-detect source type from file properties.

Uses file extension, JSON shape, and content heuristics to route
each input file to the correct extractor. Never hardcodes filenames.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)


class SourceType(str, Enum):
    """Recognized source types."""
    CSV = "csv"
    ATS_JSON = "ats_json"
    LINKEDIN_JSON = "linkedin_json"
    GITHUB = "github"
    RESUME_PDF = "resume_pdf"
    RESUME_DOCX = "resume_docx"
    NOTES_TXT = "notes_txt"
    UNKNOWN = "unknown"


# ATS JSON typically has these keys at the top level or in records
ATS_SIGNAL_KEYS = {
    "candidates", "applicants", "records", "applications",
    "candidate_id", "applicant_id", "application_id",
    "applied_date", "stage", "pipeline", "job_id",
    "source_channel", "recruiter", "hiring_manager",
}

# LinkedIn JSON typically has these keys
LINKEDIN_SIGNAL_KEYS = {
    "linkedin_url", "linkedin", "profile_url",
    "connections", "headline", "industry",
    "recommendations", "endorsements", "profile_id",
}

# GitHub user list JSON typically has these keys
GITHUB_SIGNAL_KEYS = {
    "username", "login", "github_username",
    "github_url", "github_profile",
}


def detect_source_type(path: str | Path) -> SourceType:
    """
    Detect the source type of a given file.

    Strategy:
      1. Check file extension for unambiguous types (.csv, .pdf, .docx, .txt)
      2. For .json files, inspect the JSON shape to distinguish ATS from LinkedIn

    Args:
        path: Path to the source file.

    Returns:
        SourceType enum value.
    """
    path = Path(path)

    if not path.exists():
        logger.warning("File does not exist: %s", path)
        return SourceType.UNKNOWN

    suffix = path.suffix.lower()

    # --- Unambiguous by extension ---
    if suffix == ".csv":
        return SourceType.CSV

    if suffix == ".pdf":
        return SourceType.RESUME_PDF

    if suffix in (".docx", ".doc"):
        return SourceType.RESUME_DOCX

    if suffix == ".txt":
        return SourceType.NOTES_TXT

    # --- JSON: need to inspect content ---
    if suffix == ".json":
        return _classify_json(path)

    logger.warning("Cannot determine source type for: %s", path)
    return SourceType.UNKNOWN


def _classify_json(path: Path) -> SourceType:
    """
    Classify a JSON file as ATS export or LinkedIn fixture.

    Looks at keys in the top-level object (or first record in a list)
    and scores against known signal keys for each type.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Cannot parse JSON %s: %s", path, e)
        return SourceType.UNKNOWN

    # Collect all keys to inspect
    keys_to_check: set[str] = set()

    if isinstance(data, dict):
        keys_to_check = set(k.lower() for k in data.keys())
        # Also check keys in the first list value (wrapper pattern)
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                keys_to_check.update(k.lower() for k in v[0].keys())
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        keys_to_check = set(k.lower() for k in data[0].keys())

    # Score each type
    ats_score = len(keys_to_check & ATS_SIGNAL_KEYS)
    linkedin_score = len(keys_to_check & LINKEDIN_SIGNAL_KEYS)
    github_score = len(keys_to_check & GITHUB_SIGNAL_KEYS)

    # Check filename as a hint (but don't hardcode)
    filename_lower = path.stem.lower()
    if "linkedin" in filename_lower:
        linkedin_score += 2
    if "ats" in filename_lower or "applicant" in filename_lower:
        ats_score += 2
    if "github" in filename_lower:
        github_score += 2

    # GitHub user lists take highest priority when detected
    if github_score > ats_score and github_score > linkedin_score:
        return SourceType.GITHUB
    if linkedin_score > ats_score:
        return SourceType.LINKEDIN_JSON
    if ats_score > 0:
        return SourceType.ATS_JSON

    # Default JSON → ATS (most common export format)
    return SourceType.ATS_JSON


def get_all_source_files(input_dir: str | Path) -> list[tuple[Path, SourceType]]:
    """
    Scan an input directory and classify all files.

    Args:
        input_dir: Path to directory containing source files.

    Returns:
        List of (path, source_type) tuples.
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        logger.error("Input path is not a directory: %s", input_dir)
        return []

    files: list[tuple[Path, SourceType]] = []
    for child in sorted(input_dir.iterdir()):
        if child.is_file() and not child.name.startswith("."):
            source_type = detect_source_type(child)
            if source_type != SourceType.UNKNOWN:
                files.append((child, source_type))
                logger.info("Detected %s as %s", child.name, source_type.value)
            else:
                logger.warning("Skipping unknown file type: %s", child.name)

    return files
