"""
Canonical Pydantic v2 models for the candidate ETL pipeline.

Design principle: CandidateProfile is the single source of truth.
All fields are nullable — a candidate may come from a single source
that only populates a subset of fields. The projection layer reads
from this canonical record and emits whatever the runtime config asks for.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Raw extraction types
# ---------------------------------------------------------------------------

class ExtractionMethod(str, Enum):
    """How a value was extracted from its source."""
    STRUCTURED_PARSE = "structured_parse"   # CSV column, JSON key
    REGEX = "regex"                          # regex match in free text
    HEURISTIC = "heuristic"                  # keyword / proximity guess
    API = "api"                              # fetched from an API endpoint


class RawField(BaseModel):
    """
    A single field-value pair extracted from one source.

    This is the universal intermediate representation that every extractor
    emits.  Nothing downstream mutates RawField — it is an immutable fact
    about what a source said.
    """
    field: str                                # canonical field name it maps to
    value: Any                                # raw extracted value
    source: str                               # e.g. 'csv', 'ats_json', 'github'
    source_id: str = ""                       # row id / record key inside source
    extraction_method: ExtractionMethod = ExtractionMethod.STRUCTURED_PARSE
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Provenance tracking
# ---------------------------------------------------------------------------

class Provenance(BaseModel):
    """
    Tracks where a specific field value came from.

    Stored on the canonical CandidateProfile so that downstream consumers
    (and the confidence engine) can trace every value back to its origin.
    """
    field: str                                # canonical field name
    source: str                               # which source provided this
    method: ExtractionMethod                  # how it was extracted
    raw_value: Any = None                     # the original un-normalized value
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Per-field confidence
# ---------------------------------------------------------------------------

class FieldConfidence(BaseModel):
    """Confidence score for a single canonical field."""
    field: str
    score: float = Field(ge=0.0, le=1.0)     # 0 = no confidence, 1 = certain
    source_count: int = 0                     # how many sources contributed
    has_conflict: bool = False                # True if sources disagreed


# ---------------------------------------------------------------------------
# Complex nested types (Assignment Schema)
# ---------------------------------------------------------------------------

class LocationData(BaseModel):
    """Location as { city, region, country } — country is ISO-3166 alpha-2."""
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None


class LinksData(BaseModel):
    """Links as { linkedin, github, portfolio, other[] }."""
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: List[str] = Field(default_factory=list)


class SkillEntry(BaseModel):
    """Skill as { name, confidence, sources[] } — canonical skill names."""
    name: str
    confidence: float = 1.0
    sources: List[str] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    """Experience as { company, title, start, end, summary } — dates YYYY-MM."""
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    summary: Optional[str] = None


class EducationEntry(BaseModel):
    """Education as { institution, degree, field, end_year }."""
    institution: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = Field(None, alias="field")
    end_year: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Canonical candidate profile
# ---------------------------------------------------------------------------

class CandidateProfile(BaseModel):
    """
    The single canonical representation of a candidate.

    Strictly aligns with the assignment's nested default output schema.
    Every field is Optional/empty-defaulted because a candidate may come
    from a single sparse source.
    """
    candidate_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    full_name: Optional[str] = None
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    location: Optional[LocationData] = None
    links: Optional[LinksData] = None
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: List[SkillEntry] = Field(default_factory=list)
    experience: List[ExperienceEntry] = Field(default_factory=list)
    education: List[EducationEntry] = Field(default_factory=list)

    # --- metadata (always on canonical, toggled in projection) ---
    provenance: List[Provenance] = Field(default_factory=list)
    overall_confidence: float = 0.0
    field_confidences: List[FieldConfidence] = Field(default_factory=list)

    # --- internal (not serialized by default) ---
    _conflicts: Dict[str, bool] = {}
    _contributing_sources: set = set()

    model_config = ConfigDict(arbitrary_types_allowed=True)
