"""
Merge engine — entity resolution and field-level conflict resolution.

Two-phase process:
  1. Clustering: Group RawField lists into per-candidate clusters using
     email as primary key, then fallback to normalized phone, then fuzzy
     name+company match.
  2. Field resolution: For each canonical field, pick a winner using a
     source-priority order with most-recent-wins as tiebreaker. Keep all
     contributing sources in provenance.

The merge engine also transforms flat extracted fields into the nested
structures required by the canonical CandidateProfile schema.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from src.schema import (
    RawField,
    CandidateProfile,
    Provenance,
    ExtractionMethod,
    LocationData,
    LinksData,
    SkillEntry,
    ExperienceEntry,
    EducationEntry,
)
from src.normalize import (
    normalize_email,
    normalize_phone,
    normalize_name,
    normalize_country,
    normalize_skills,
    normalize_field,
)

logger = logging.getLogger(__name__)


# Source priority order — higher index = higher priority
# Structured sources rank above unstructured.
SOURCE_PRIORITY: dict[str, int] = {
    "notes": 1,
    "resume": 2,
    "linkedin": 3,
    "github": 4,
    "ats_json": 5,
    "csv": 6,
}

# Fuzzy match threshold for name+company matching
FUZZY_THRESHOLD = 85


# ---------------------------------------------------------------------------
# Phase 1 — Clustering
# ---------------------------------------------------------------------------

class CandidateCluster:
    """A cluster of RawField lists that belong to the same candidate."""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        self.raw_fields: List[RawField] = []
        self.emails: set[str] = set()
        self.phones: set[str] = set()
        self.names: set[str] = set()
        self.companies: set[str] = set()
        self.sources: set[str] = set()

    def add_fields(self, fields: List[RawField]) -> None:
        """Add a batch of raw fields to this cluster."""
        self.raw_fields.extend(fields)
        for f in fields:
            self.sources.add(f.source)
            if f.field == "email" and f.value:
                self.emails.add(normalize_email(str(f.value)) or str(f.value).lower())
            elif f.field == "phone" and f.value:
                norm_phone = normalize_phone(str(f.value))
                if norm_phone:
                    self.phones.add(norm_phone)
            elif f.field == "full_name" and f.value:
                self.names.add(str(f.value).strip().lower())
            elif f.field == "current_company" and f.value:
                self.companies.add(str(f.value).strip().lower())

    def matches_by_email(self, other: "CandidateCluster") -> bool:
        """Check if two clusters share an email."""
        return bool(self.emails & other.emails)

    def matches_by_phone(self, other: "CandidateCluster") -> bool:
        """Check if two clusters share a normalized phone number."""
        return bool(self.phones & other.phones)

    def matches_by_fuzzy(self, other: "CandidateCluster") -> bool:
        """
        Check if two clusters match by fuzzy name+company.
        Both name AND company must fuzzy-match above threshold.
        """
        if not self.names or not other.names:
            return False

        # Name must match
        name_match = False
        for n1 in self.names:
            for n2 in other.names:
                if fuzz.WRatio(n1, n2) >= FUZZY_THRESHOLD:
                    name_match = True
                    break
            if name_match:
                break

        if not name_match:
            return False

        # If both have companies, they must also match
        if self.companies and other.companies:
            for c1 in self.companies:
                for c2 in other.companies:
                    if fuzz.WRatio(c1, c2) >= FUZZY_THRESHOLD:
                        return True
            return False

        # If only one or neither has company, name match alone is sufficient
        return name_match

    def merge_with(self, other: "CandidateCluster") -> None:
        """Absorb another cluster into this one."""
        self.raw_fields.extend(other.raw_fields)
        self.emails.update(other.emails)
        self.phones.update(other.phones)
        self.names.update(other.names)
        self.companies.update(other.companies)
        self.sources.update(other.sources)


def cluster_candidates(
    all_records: Dict[str, List[RawField]],
) -> List[CandidateCluster]:
    """
    Group raw records into candidate clusters.

    Match cascade:
      1. Email (exact, case-insensitive) — strongest signal
      2. Normalized phone (exact)
      3. Fuzzy name + company (rapidfuzz, threshold >= 85)
    """
    clusters: List[CandidateCluster] = []

    for record_id, fields in all_records.items():
        new_cluster = CandidateCluster(cluster_id=record_id)
        new_cluster.add_fields(fields)

        # Try to find an existing cluster this belongs to
        merged = False
        for existing in clusters:
            if existing.matches_by_email(new_cluster):
                existing.merge_with(new_cluster)
                merged = True
                logger.debug("Merged %s into cluster via email match", record_id)
                break
            if existing.matches_by_phone(new_cluster):
                existing.merge_with(new_cluster)
                merged = True
                logger.debug("Merged %s into cluster via phone match", record_id)
                break
            if existing.matches_by_fuzzy(new_cluster):
                existing.merge_with(new_cluster)
                merged = True
                logger.debug("Merged %s into cluster via fuzzy name+company match", record_id)
                break

        if not merged:
            clusters.append(new_cluster)

    logger.info(
        "Clustering: %d records -> %d unique candidates",
        len(all_records), len(clusters),
    )
    return clusters


# ---------------------------------------------------------------------------
# Phase 2 — Field conflict resolution
# ---------------------------------------------------------------------------

def _resolve_field(
    field_name: str,
    candidates: List[RawField],
    provenance_name: str | None = None,
) -> tuple[Optional[Any], List[Provenance], bool]:
    """
    Resolve a single canonical field from multiple raw field values.

    Args:
        field_name: The raw field name from extractors.
        candidates: List of RawField values for this field.
        provenance_name: Optional canonical name for provenance records.
                         If None, uses field_name.

    Strategy:
      1. Sort by source priority (highest first)
      2. Tiebreak by timestamp (most recent wins)
      3. Pick the top-ranked value as the winner
      4. Track all contributing values in provenance
      5. Flag if there's a conflict (different normalized values)
    """
    if not candidates:
        return None, [], False

    prov_field = provenance_name or field_name

    # Sort: highest source priority first, then most recent timestamp
    sorted_candidates = sorted(
        candidates,
        key=lambda f: (SOURCE_PRIORITY.get(f.source, 0), f.timestamp),
        reverse=True,
    )

    winner = sorted_candidates[0]
    resolved_value = winner.value

    # Build provenance for all contributing values
    provenance = [
        Provenance(
            field=prov_field,
            source=f.source,
            method=f.extraction_method,
            raw_value=f.value,
            timestamp=f.timestamp,
        )
        for f in sorted_candidates
    ]

    # Detect conflict: are there different values across sources?
    has_conflict = False
    if len(sorted_candidates) > 1:
        if isinstance(resolved_value, list):
            value_sets = [
                frozenset(str(v).lower() for v in f.value)
                if isinstance(f.value, list) else frozenset([str(f.value).lower()])
                for f in sorted_candidates
            ]
            has_conflict = len(set(value_sets)) > 1
        else:
            normalized_values = set()
            for f in sorted_candidates:
                v = str(f.value).strip().lower() if f.value else ""
                normalized_values.add(v)
            has_conflict = len(normalized_values) > 1

    if has_conflict:
        logger.debug(
            "Conflict on field '%s': %d different values across %d sources",
            prov_field,
            len(set(str(f.value) for f in sorted_candidates)),
            len(sorted_candidates),
        )

    return resolved_value, provenance, has_conflict


def resolve_cluster(
    cluster: CandidateCluster,
    apply_normalization: bool = True,
) -> CandidateProfile:
    """
    Resolve a candidate cluster into a canonical CandidateProfile.

    Extractors emit flat field names (email, phone, location, linkedin_url, etc.).
    This function transforms them into the nested structures required by the
    assignment schema (emails[], phones[], LocationData, LinksData, etc.).
    """
    # Group raw fields by canonical field name
    fields_by_name: Dict[str, List[RawField]] = defaultdict(list)
    for raw_field in cluster.raw_fields:
        fields_by_name[raw_field.field].append(raw_field)

    all_provenance: list[Provenance] = []
    conflicts: dict[str, bool] = {}

    # --- full_name (pick winner) ---
    full_name = None
    if "full_name" in fields_by_name:
        val, prov, conflict = _resolve_field("full_name", fields_by_name["full_name"])
        if val and apply_normalization:
            val = normalize_name(val)
        full_name = val
        all_provenance.extend(prov)
        conflicts["full_name"] = conflict

    # --- emails[] (union, deduplicated, normalized) ---
    emails: list[str] = []
    email_seen: set[str] = set()
    if "email" in fields_by_name:
        for rf in fields_by_name["email"]:
            e = normalize_email(str(rf.value)) if apply_normalization else str(rf.value)
            if e and e not in email_seen:
                emails.append(e)
                email_seen.add(e)
            all_provenance.append(Provenance(
                field="emails", source=rf.source, method=rf.extraction_method,
                raw_value=rf.value, timestamp=rf.timestamp,
            ))
        conflicts["emails"] = len(fields_by_name["email"]) > 1

    # --- phones[] (union, deduplicated, E.164 normalized) ---
    phones: list[str] = []
    phone_seen: set[str] = set()
    if "phone" in fields_by_name:
        for rf in fields_by_name["phone"]:
            p = normalize_phone(str(rf.value)) if apply_normalization else str(rf.value)
            if p and p not in phone_seen:
                phones.append(p)
                phone_seen.add(p)
            all_provenance.append(Provenance(
                field="phones", source=rf.source, method=rf.extraction_method,
                raw_value=rf.value, timestamp=rf.timestamp,
            ))
        conflicts["phones"] = len(set(str(rf.value) for rf in fields_by_name["phone"])) > 1

    # --- location { city, region, country } ---
    location_data = None
    loc_fields = fields_by_name.get("location", [])
    country_fields = fields_by_name.get("country", [])
    if loc_fields or country_fields:
        # Resolve location string
        loc_str = None
        if loc_fields:
            val, prov, conflict = _resolve_field("location", loc_fields)
            loc_str = str(val) if val else None
            all_provenance.extend(prov)
            conflicts["location"] = conflict

        # Resolve country
        country_str = None
        if country_fields:
            val, prov, conflict = _resolve_field("country", country_fields)
            country_str = str(val) if val else None
            all_provenance.extend(prov)
            conflicts["country"] = conflict

        # Parse location string into city/region
        city, region = None, None
        if loc_str:
            parts = [p.strip() for p in loc_str.split(",")]
            city = parts[0] if parts else None
            region = parts[1] if len(parts) > 1 else None

        # Normalize country to ISO-3166
        country_code = None
        if country_str and apply_normalization:
            country_code = normalize_country(country_str)
        elif country_str:
            country_code = country_str

        location_data = LocationData(city=city, region=region, country=country_code)

    # --- links { linkedin, github, portfolio, other[] } ---
    links_data = None
    linkedin_url = None
    github_url = None
    if "linkedin_url" in fields_by_name:
        val, prov, conflict = _resolve_field("linkedin_url", fields_by_name["linkedin_url"], provenance_name="links")
        linkedin_url = str(val) if val else None
        all_provenance.extend(prov)
        conflicts["links"] = conflict
    if "github_url" in fields_by_name:
        val, prov, conflict = _resolve_field("github_url", fields_by_name["github_url"], provenance_name="links")
        github_url = str(val) if val else None
        all_provenance.extend(prov)
    # Handle "links" dict from resume extractor
    if "links" in fields_by_name:
        for rf in fields_by_name["links"]:
            if isinstance(rf.value, dict):
                linkedin_url = linkedin_url or rf.value.get("linkedin")
                github_url = github_url or rf.value.get("github")
            all_provenance.append(Provenance(
                field="links", source=rf.source, method=rf.extraction_method,
                raw_value=rf.value, timestamp=rf.timestamp,
            ))
    if linkedin_url or github_url:
        links_data = LinksData(linkedin=linkedin_url, github=github_url)

    # --- headline (from current_title) ---
    headline = None
    if "current_title" in fields_by_name:
        val, prov, conflict = _resolve_field("current_title", fields_by_name["current_title"], provenance_name="headline")
        headline = str(val) if val else None
        all_provenance.extend(prov)
        conflicts["headline"] = conflict

    # --- years_experience ---
    years_exp = None
    if "years_of_experience" in fields_by_name:
        val, prov, conflict = _resolve_field("years_of_experience", fields_by_name["years_of_experience"], provenance_name="years_experience")
        if val is not None:
            try:
                years_exp = float(val)
            except (ValueError, TypeError):
                pass
        all_provenance.extend(prov)
        conflicts["years_experience"] = conflict

    # --- skills [{ name, confidence, sources[] }] ---
    skill_entries: list[SkillEntry] = []
    if "skills" in fields_by_name:
        all_skill_strs: list[str] = []
        skill_sources: dict[str, set[str]] = defaultdict(set)
        for rf in fields_by_name["skills"]:
            if isinstance(rf.value, list):
                for s in rf.value:
                    all_skill_strs.append(str(s))
                    skill_sources[str(s).lower()].add(rf.source)
            elif rf.value:
                all_skill_strs.append(str(rf.value))
                skill_sources[str(rf.value).lower()].add(rf.source)
            all_provenance.append(Provenance(
                field="skills", source=rf.source, method=rf.extraction_method,
                raw_value=rf.value, timestamp=rf.timestamp,
            ))

        # Normalize and deduplicate
        if apply_normalization:
            canonical_skills = normalize_skills(all_skill_strs)
        else:
            canonical_skills = list(dict.fromkeys(all_skill_strs))  # dedup preserving order

        for s in canonical_skills:
            sources_for_skill = skill_sources.get(s.lower(), set())
            # Also check all alias matches
            for raw_s, srcs in skill_sources.items():
                if raw_s == s.lower():
                    sources_for_skill.update(srcs)
            skill_entries.append(SkillEntry(
                name=s,
                confidence=min(1.0, 0.5 + 0.15 * len(sources_for_skill)),
                sources=sorted(sources_for_skill),
            ))
        conflicts["skills"] = len(fields_by_name["skills"]) > 1

    # --- experience [{ company, title, start, end, summary }] ---
    experience_entries: list[ExperienceEntry] = []
    if "current_company" in fields_by_name or "current_title" in fields_by_name:
        company_val = None
        if "current_company" in fields_by_name:
            val, prov, conflict = _resolve_field("current_company", fields_by_name["current_company"], provenance_name="experience")
            company_val = str(val) if val else None
            all_provenance.extend(prov)
            conflicts["experience"] = conflict

        experience_entries.append(ExperienceEntry(
            company=company_val,
            title=headline,
        ))

    # --- education [{ institution, degree, field, end_year }] ---
    education_entries: list[EducationEntry] = []
    if "education" in fields_by_name:
        val, prov, conflict = _resolve_field("education", fields_by_name["education"])
        all_provenance.extend(prov)
        conflicts["education"] = conflict
        if val:
            edu_str = str(val)
            # Try to parse "Degree, Institution" or "Institution YEAR"
            edu_entries_raw = [e.strip() for e in edu_str.split(";") if e.strip()]
            for entry in edu_entries_raw:
                parts = [p.strip() for p in entry.split(",")]
                if len(parts) >= 2:
                    education_entries.append(EducationEntry(
                        institution=parts[-1].strip() if len(parts) > 1 else None,
                        degree=parts[0].strip(),
                    ))
                else:
                    education_entries.append(EducationEntry(institution=entry))

    # --- summary (not in assignment schema but useful for provenance) ---
    if "summary" in fields_by_name:
        val, prov, conflict = _resolve_field("summary", fields_by_name["summary"])
        all_provenance.extend(prov)

    # --- certifications (fold into provenance) ---
    if "certifications" in fields_by_name:
        for rf in fields_by_name["certifications"]:
            all_provenance.append(Provenance(
                field="certifications", source=rf.source, method=rf.extraction_method,
                raw_value=rf.value, timestamp=rf.timestamp,
            ))

    # Build the CandidateProfile
    profile = CandidateProfile(
        full_name=full_name,
        emails=emails,
        phones=phones,
        location=location_data,
        links=links_data,
        headline=headline,
        years_experience=years_exp,
        skills=skill_entries,
        experience=experience_entries,
        education=education_entries,
        provenance=all_provenance,
    )

    # Store conflict info for confidence scoring
    profile._conflicts = conflicts

    return profile


# ---------------------------------------------------------------------------
# Main merge entry point
# ---------------------------------------------------------------------------

def merge_all(
    all_records: Dict[str, List[RawField]],
    apply_normalization: bool = True,
) -> List[CandidateProfile]:
    """
    Full merge pipeline: cluster -> resolve -> return canonical profiles.
    """
    clusters = cluster_candidates(all_records)
    profiles = []

    for cluster in clusters:
        try:
            profile = resolve_cluster(cluster, apply_normalization)
            profiles.append(profile)
        except Exception as e:
            logger.error("Error resolving cluster %s: %s", cluster.cluster_id, e)
            continue

    logger.info("Merge complete: %d canonical profiles", len(profiles))
    return profiles
