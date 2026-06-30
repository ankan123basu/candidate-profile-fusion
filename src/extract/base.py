"""
Base extractor interface.

Every source-specific extractor inherits from BaseExtractor and
implements the `extract` method, which returns a list of RawField
objects grouped by candidate (keyed by whatever ID the source uses).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List

from src.schema import RawField

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """
    Abstract base class for all source extractors.

    Subclasses must implement `extract()` which returns a dict mapping
    source-local candidate IDs to lists of RawField values.
    """

    source_name: str = "unknown"

    @abstractmethod
    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        """
        Parse the source and return extracted fields.

        Args:
            source_path: Path to the source file or URL.

        Returns:
            Dict mapping source-local candidate IDs (e.g. row number, record
            key) to a list of RawField objects.  Each RawField carries
            the canonical field name it maps to, the raw value, the source
            tag, and the extraction method.
        """
        ...

    def _safe_strip(self, value: str | None) -> str | None:
        """Strip whitespace, return None for empty strings."""
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped if stripped else None
