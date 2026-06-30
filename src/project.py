"""
Projection engine — config-driven view over the canonical profile.

KEY DESIGN PRINCIPLE: The canonical CandidateProfile is the source of truth.
This projection layer is a PURE READ-ONLY VIEW. It never mutates the
canonical record — it reads fields via 'from' paths, applies optional
normalization, and emits a reshaped dict per the runtime config.

Config schema (supports two key formats for field naming):
{
  "fields": [
    {"path": "output_name", "from": "canonical_field", "type": "string", "normalize": "E164"},
    {"name": "output_name", "from": "canonical_field", "type": "string", "normalize": true},
    ...
  ],
  "include_confidence": true/false,
  "include_provenance": true/false,
  "on_missing": "null" | "omit" | "error",
  "normalize_defaults": true/false
}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.schema import CandidateProfile
from src.normalize import normalize_field

logger = logging.getLogger(__name__)


class ProjectionError(Exception):
    """Raised when a required field is missing and on_missing='error'."""
    pass


def _get_nested_value(obj: Any, dot_path: str) -> Any:
    """
    Retrieve a value from an object using a dot-separated path.

    Supports:
      "emails"          → obj.emails
      "emails[0]"       → obj.emails[0]  (first element)
      "skills[].name"   → [s.name for s in obj.skills]  (map over list)
      "location.city"   → obj.location.city
    """
    import re
    parts = dot_path.split(".")
    current = obj

    for part in parts:
        if current is None:
            return None

        # Check for array indexing: field[0], field[1], etc.
        idx_match = re.match(r'^(\w+)\[(\d+)\]$', part)
        # Check for array mapping: field[].subfield (handled by remaining parts)
        map_match = re.match(r'^(\w+)\[\]$', part)

        if idx_match:
            field_name = idx_match.group(1)
            index = int(idx_match.group(2))
            if isinstance(current, dict):
                arr = current.get(field_name, [])
            elif hasattr(current, field_name):
                arr = getattr(current, field_name)
            else:
                return None
            if isinstance(arr, list) and index < len(arr):
                current = arr[index]
            else:
                return None
        elif map_match:
            field_name = map_match.group(1)
            if isinstance(current, dict):
                arr = current.get(field_name, [])
            elif hasattr(current, field_name):
                arr = getattr(current, field_name)
            else:
                return None
            if not isinstance(arr, list):
                return None
            # Get remaining path parts and map over the array
            remaining = ".".join(parts[parts.index(part)+1:])
            if remaining:
                return [_get_nested_value(item, remaining) for item in arr]
            return arr
        elif isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

    return current


def project_profile(
    profile: CandidateProfile,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Project a canonical profile through the runtime config.

    Args:
        profile: The canonical CandidateProfile (source of truth).
        config: Runtime projection config dict.

    Returns:
        A reshaped dict containing only the fields/values the config requests.

    Raises:
        ProjectionError: If on_missing='error' and a required field is None.
    """
    on_missing = config.get("on_missing", "null")
    include_confidence = config.get("include_confidence", False)
    include_provenance = config.get("include_provenance", False)
    normalize_defaults = config.get("normalize_defaults", True)
    field_specs = config.get("fields", [])

    result: Dict[str, Any] = {}

    for spec in field_specs:
        # Support both 'name' and 'path' keys (assignment uses 'path')
        output_name = spec.get("name") or spec.get("path", "")
        if not output_name:
            logger.warning("Field spec missing both 'name' and 'path', skipping")
            continue
        from_path = spec.get("from", output_name)  # default: same name

        # Support normalize as bool or string ("E164", "canonical", etc.)
        normalize_spec = spec.get("normalize", normalize_defaults)
        should_normalize = bool(normalize_spec)  # truthy check works for both

        # Read value from canonical profile
        value = _get_nested_value(profile, from_path)

        # Treat empty lists as missing
        if isinstance(value, list) and len(value) == 0:
            value = None

        # Handle missing values
        if value is None:
            if on_missing == "error":
                raise ProjectionError(
                    f"Required field '{output_name}' (from '{from_path}') is missing"
                )
            elif on_missing == "omit":
                continue  # skip this field entirely
            else:  # "null"
                result[output_name] = None
                continue

        # Apply normalization if requested
        if should_normalize:
            value = normalize_field(from_path, value)

        # Serialize Pydantic models to dicts for JSON output
        from pydantic import BaseModel
        if isinstance(value, BaseModel):
            value = value.model_dump(by_alias=True, exclude_none=False)
        elif isinstance(value, list):
            serialized = []
            for item in value:
                if isinstance(item, BaseModel):
                    serialized.append(item.model_dump(by_alias=True, exclude_none=False))
                else:
                    serialized.append(item)
            value = serialized

        result[output_name] = value

    # Optionally include confidence scores
    if include_confidence:
        confidence_dict = {
            "overall": profile.overall_confidence,
            "fields": {},
        }
        for fc in profile.field_confidences:
            confidence_dict["fields"][fc.field] = {
                "score": fc.score,
                "source_count": fc.source_count,
                "has_conflict": fc.has_conflict,
            }
        result["_confidence"] = confidence_dict

    # Optionally include provenance
    if include_provenance:
        provenance_list = []
        for p in profile.provenance:
            provenance_list.append({
                "field": p.field,
                "source": p.source,
                "method": p.method.value,
                "raw_value": p.raw_value if not isinstance(p.raw_value, list) else p.raw_value,
            })
        result["_provenance"] = provenance_list

    return result


def project_all(
    profiles: List[CandidateProfile],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Project a list of canonical profiles through the config.

    Profiles that fail projection (e.g. on_missing='error') are logged
    and skipped.

    Returns:
        List of projected dicts.
    """
    results: List[Dict[str, Any]] = []

    for profile in profiles:
        try:
            projected = project_profile(profile, config)
            results.append(projected)
        except ProjectionError as e:
            logger.error("Projection error for %s: %s",
                         (profile.emails[0] if profile.emails else profile.full_name or "unknown"), e)
        except Exception as e:
            logger.error("Unexpected projection error: %s", e)

    logger.info("Projected %d / %d profiles", len(results), len(profiles))
    return results
