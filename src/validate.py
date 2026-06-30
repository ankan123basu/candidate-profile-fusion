"""
Validation layer — validates both canonical records and projected output.

Two validation targets:
  1. Canonical CandidateProfile — validated by Pydantic automatically
  2. Projected output — validated against a JSON schema dynamically
     generated from the runtime config's field list and types

Returns structured validation errors; never crashes the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import jsonschema

from src.schema import CandidateProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON type mapping for config→schema generation
# ---------------------------------------------------------------------------

TYPE_MAP: dict[str, dict] = {
    "string": {"type": ["string", "null"]},
    "number": {"type": ["number", "null"]},
    "integer": {"type": ["integer", "null"]},
    "boolean": {"type": ["boolean", "null"]},
    "array": {"type": ["array", "null"]},
    "object": {"type": ["object", "null"]},
    "string[]": {"type": ["array", "null"]},
}


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

class ValidationResult:
    """Structured validation result."""

    def __init__(self):
        self.is_valid: bool = True
        self.errors: List[Dict[str, str]] = []

    def add_error(self, field: str, message: str) -> None:
        self.is_valid = False
        self.errors.append({"field": field, "message": message})

    def __repr__(self) -> str:
        if self.is_valid:
            return "ValidationResult(valid=True)"
        return f"ValidationResult(valid=False, errors={len(self.errors)})"


# ---------------------------------------------------------------------------
# Canonical profile validation (via Pydantic)
# ---------------------------------------------------------------------------

def validate_canonical(profile: CandidateProfile) -> ValidationResult:
    """
    Validate a canonical CandidateProfile.

    Since the profile is already a Pydantic model, construction itself
    validates types. This function performs additional business rule checks.
    """
    result = ValidationResult()

    # Business rule: a profile must have at least one identity field
    if not profile.full_name and not profile.emails and not profile.phones:
        result.add_error(
            "identity",
            "Profile must have at least one of: full_name, emails, phones"
        )

    # Business rule: email format (if present)
    for em in profile.emails:
        if "@" not in em:
            result.add_error("emails", f"Invalid email format: {em}")

    # Business rule: confidence must be in range
    if not (0.0 <= profile.overall_confidence <= 1.0):
        result.add_error(
            "overall_confidence",
            f"Confidence out of range [0, 1]: {profile.overall_confidence}"
        )

    # Business rule: years_experience must be non-negative
    if profile.years_experience is not None and profile.years_experience < 0:
        result.add_error(
            "years_experience",
            f"Negative years of experience: {profile.years_experience}"
        )

    return result


# ---------------------------------------------------------------------------
# Projected output validation (via jsonschema)
# ---------------------------------------------------------------------------

def generate_schema_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dynamically generate a JSON schema from the runtime config.

    Each field in config["fields"] becomes a property in the schema
    with the type specified by the field's "type" attribute.
    """
    properties: Dict[str, Any] = {}
    required_fields: List[str] = []

    for field_spec in config.get("fields", []):
        # Support both 'name' and 'path' keys (assignment uses 'path')
        name = field_spec.get("name") or field_spec.get("path", "")
        if not name:
            continue
        field_type = field_spec.get("type", "string")
        properties[name] = TYPE_MAP.get(field_type, {"type": ["string", "null"]})

        # Fields are required unless on_missing allows nulls
        on_missing = config.get("on_missing", "null")
        if on_missing == "error":
            required_fields.append(name)

    # Allow confidence and provenance metadata
    if config.get("include_confidence"):
        properties["_confidence"] = {"type": "object"}
    if config.get("include_provenance"):
        properties["_provenance"] = {"type": "array"}

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required_fields:
        schema["required"] = required_fields

    return schema


def validate_projected(
    projected: Dict[str, Any],
    config: Dict[str, Any],
) -> ValidationResult:
    """
    Validate a projected output dict against a schema derived from the config.

    Args:
        projected: The projected output dict.
        config: The runtime projection config.

    Returns:
        ValidationResult with any schema violations.
    """
    result = ValidationResult()
    schema = generate_schema_from_config(config)

    try:
        jsonschema.validate(instance=projected, schema=schema)
    except jsonschema.ValidationError as e:
        result.add_error(
            field=".".join(str(p) for p in e.absolute_path) or "root",
            message=e.message,
        )
    except jsonschema.SchemaError as e:
        result.add_error(
            field="schema",
            message=f"Invalid schema: {e.message}",
        )

    return result


def validate_all_projected(
    projected_list: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> List[ValidationResult]:
    """
    Validate a list of projected outputs.

    Returns one ValidationResult per projected record.
    """
    results = []
    for i, projected in enumerate(projected_list):
        vr = validate_projected(projected, config)
        if not vr.is_valid:
            logger.warning("Validation errors in record %d: %s", i, vr.errors)
        results.append(vr)

    valid_count = sum(1 for r in results if r.is_valid)
    logger.info("Validation: %d/%d records valid", valid_count, len(results))
    return results
