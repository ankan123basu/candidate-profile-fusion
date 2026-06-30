"""
Normalizers — pure functions that transform raw extracted values
into canonical representations.

Design principles:
  - Every function is a pure function (no side effects, no shared state)
  - Every function returns None on unparseable input rather than throwing
  - Each function is independently unit-testable
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skill taxonomy — canonical names → aliases
# ---------------------------------------------------------------------------
SKILL_TAXONOMY: dict[str, list[str]] = {
    "Python": ["python", "python3", "py"],
    "JavaScript": ["javascript", "js", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "Java": ["java", "jvm"],
    "C++": ["c++", "cpp", "cplusplus"],
    "C#": ["c#", "csharp", "c sharp"],
    "Go": ["go", "golang"],
    "Rust": ["rust", "rust-lang"],
    "Ruby": ["ruby", "rb"],
    "PHP": ["php"],
    "Swift": ["swift"],
    "Kotlin": ["kotlin", "kt"],
    "Scala": ["scala"],
    "R": ["r", "r-lang", "rlang"],
    "SQL": ["sql", "structured query language"],
    "NoSQL": ["nosql", "no-sql"],
    "MongoDB": ["mongodb", "mongo"],
    "PostgreSQL": ["postgresql", "postgres", "psql", "pg"],
    "MySQL": ["mysql"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic", "es"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Docker": ["docker", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform", "tf"],
    "Jenkins": ["jenkins"],
    "React": ["react", "reactjs", "react.js"],
    "Angular": ["angular", "angularjs", "angular.js"],
    "Vue.js": ["vue", "vuejs", "vue.js"],
    "Node.js": ["node", "nodejs", "node.js"],
    "Django": ["django"],
    "Flask": ["flask"],
    "Spring": ["spring", "spring boot", "springboot"],
    "FastAPI": ["fastapi", "fast api"],
    "Express": ["express", "expressjs", "express.js"],
    "Next.js": ["next", "nextjs", "next.js"],
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "dl"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "cv", "image recognition"],
    "TensorFlow": ["tensorflow", "tf"],
    "PyTorch": ["pytorch", "torch"],
    "Scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3", "stylesheet"],
    "Sass": ["sass", "scss"],
    "GraphQL": ["graphql", "gql"],
    "REST": ["rest", "restful", "rest api"],
    "Git": ["git", "version control"],
    "Linux": ["linux", "unix"],
    "Agile": ["agile", "agile methodology"],
    "Scrum": ["scrum"],
    "CI/CD": ["ci/cd", "cicd", "ci cd", "continuous integration"],
    "DevOps": ["devops", "dev ops"],
    "Figma": ["figma"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Apache Spark": ["spark", "apache spark", "pyspark"],
    "Hadoop": ["hadoop", "hdfs"],
    "Kafka": ["kafka", "apache kafka"],
    "Microservices": ["microservices", "micro services", "micro-services"],
    "API Design": ["api design", "api architecture"],
    "System Design": ["system design", "systems design"],
    "Data Structures": ["data structures", "dsa", "data structures and algorithms"],
}

# Build reverse lookup: lowercase alias → canonical name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical_name, aliases in SKILL_TAXONOMY.items():
    _ALIAS_TO_CANONICAL[canonical_name.lower()] = canonical_name
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical_name


# ---------------------------------------------------------------------------
# Phone normalization → E.164
# ---------------------------------------------------------------------------

def normalize_phone(raw: str | None, default_region: str = "US") -> Optional[str]:
    """
    Normalize a phone number to E.164 format.

    Args:
        raw: Raw phone string (any format).
        default_region: ISO 3166-1 alpha-2 country code for numbers
                        missing a country code.

    Returns:
        E.164 formatted string (e.g. "+14155552671") or None if unparseable.
    """
    if not raw or not str(raw).strip():
        return None

    try:
        import phonenumbers
        parsed = phonenumbers.parse(str(raw).strip(), default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
        else:
            logger.debug("Invalid phone number: %s", raw)
            return None
    except Exception as e:
        logger.debug("Cannot parse phone '%s': %s", raw, e)
        return None


# ---------------------------------------------------------------------------
# Date normalization → YYYY-MM
# ---------------------------------------------------------------------------

def normalize_date(raw: str | None) -> Optional[str]:
    """
    Normalize a date string to YYYY-MM format.

    Handles various formats: "Jan 2023", "2023-01-15", "01/2023",
    "January 2023", "2023", etc.

    Returns:
        "YYYY-MM" string or None if unparseable.
    """
    if not raw or not str(raw).strip():
        return None

    try:
        from dateutil import parser as dateutil_parser
        parsed = dateutil_parser.parse(str(raw).strip(), fuzzy=True)
        return parsed.strftime("%Y-%m")
    except Exception as e:
        logger.debug("Cannot parse date '%s': %s", raw, e)

        # Fallback: try to extract just a year
        year_match = re.search(r'\b(19|20)\d{2}\b', str(raw))
        if year_match:
            return f"{year_match.group()}-01"

        return None


# ---------------------------------------------------------------------------
# Country normalization → ISO 3166-1 alpha-2
# ---------------------------------------------------------------------------

def normalize_country(raw: str | None) -> Optional[str]:
    """
    Normalize a country name or code to ISO 3166-1 alpha-2.

    Handles: full names ("United States"), common abbreviations ("USA"),
    alpha-2 ("US"), alpha-3 ("USA"), fuzzy matches ("Unites States").

    Returns:
        Two-letter country code (e.g. "US") or None if unrecognizable.
    """
    if not raw or not str(raw).strip():
        return None

    raw_clean = str(raw).strip()

    try:
        import pycountry

        # Direct alpha-2 lookup
        if len(raw_clean) == 2:
            country = pycountry.countries.get(alpha_2=raw_clean.upper())
            if country:
                return country.alpha_2

        # Direct alpha-3 lookup
        if len(raw_clean) == 3:
            country = pycountry.countries.get(alpha_3=raw_clean.upper())
            if country:
                return country.alpha_2

        # Name lookup (exact)
        country = pycountry.countries.get(name=raw_clean)
        if country:
            return country.alpha_2

        # Common aliases
        ALIASES = {
            "usa": "US", "united states": "US", "united states of america": "US",
            "america": "US", "u.s.": "US", "u.s.a.": "US",
            "uk": "GB", "united kingdom": "GB", "great britain": "GB",
            "england": "GB", "britain": "GB",
            "uae": "AE", "united arab emirates": "AE",
            "south korea": "KR", "korea": "KR",
            "russia": "RU", "russian federation": "RU",
            "taiwan": "TW",
            "czechia": "CZ", "czech republic": "CZ",
        }
        alias_result = ALIASES.get(raw_clean.lower())
        if alias_result:
            return alias_result

        # Fuzzy search using pycountry
        results = pycountry.countries.search_fuzzy(raw_clean)
        if results:
            return results[0].alpha_2

    except LookupError:
        logger.debug("Country not found: %s", raw)
    except Exception as e:
        logger.debug("Cannot normalize country '%s': %s", raw, e)

    return None


# ---------------------------------------------------------------------------
# Skill normalization → canonical taxonomy name
# ---------------------------------------------------------------------------

def normalize_skill(raw: str | None, taxonomy: dict[str, str] | None = None) -> Optional[str]:
    """
    Normalize a skill name to its canonical form using a taxonomy.

    First tries exact alias match, then falls back to fuzzy matching
    via rapidfuzz (threshold ≥ 80).

    Args:
        raw: Raw skill string.
        taxonomy: Optional override for the alias→canonical lookup.
                  Defaults to the built-in _ALIAS_TO_CANONICAL.

    Returns:
        Canonical skill name or None if no match found.
    """
    if not raw or not str(raw).strip():
        return None

    raw_lower = str(raw).strip().lower()
    lookup = taxonomy or _ALIAS_TO_CANONICAL

    # Exact match
    if raw_lower in lookup:
        return lookup[raw_lower]

    # Fuzzy match
    try:
        from rapidfuzz import process, fuzz
        result = process.extractOne(
            raw_lower,
            lookup.keys(),
            scorer=fuzz.WRatio,
            score_cutoff=80,
        )
        if result:
            matched_alias, score, _ = result
            return lookup[matched_alias]
    except Exception as e:
        logger.debug("Fuzzy match error for skill '%s': %s", raw, e)

    return None


def normalize_skills(raw_skills: list[str] | None, taxonomy: dict[str, str] | None = None) -> list[str]:
    """
    Normalize a list of skill strings. Deduplicates after normalization.

    Returns:
        List of unique canonical skill names (order preserved).
    """
    if not raw_skills:
        return []

    seen: set[str] = set()
    result: list[str] = []
    for skill in raw_skills:
        canonical = normalize_skill(skill, taxonomy)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)

    return result


# ---------------------------------------------------------------------------
# Email normalization
# ---------------------------------------------------------------------------

def normalize_email(raw: str | None) -> Optional[str]:
    """
    Normalize an email address: lowercase, strip whitespace.

    Returns:
        Lowercase email string or None if invalid.
    """
    if not raw or not str(raw).strip():
        return None

    email = str(raw).strip().lower()

    # Basic validation
    if "@" not in email or "." not in email.split("@")[-1]:
        logger.debug("Invalid email: %s", raw)
        return None

    return email


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

def normalize_name(raw: str | None) -> Optional[str]:
    """
    Normalize a name: strip whitespace, title case, collapse spaces.

    Returns:
        Title-cased name or None if empty.
    """
    if not raw or not str(raw).strip():
        return None

    name = str(raw).strip()
    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name)
    # Title case
    name = name.title()

    return name


# ---------------------------------------------------------------------------
# Aggregate normalizer — normalize all fields on a dict
# ---------------------------------------------------------------------------

FIELD_NORMALIZERS = {
    "phone": normalize_phone,
    "email": normalize_email,
    "full_name": normalize_name,
    "country": normalize_country,
    "last_updated": normalize_date,
}


def normalize_field(field_name: str, value, **kwargs) -> any:
    """
    Normalize a single field value based on field name.

    Args:
        field_name: Canonical field name.
        value: Raw value.
        **kwargs: Extra args passed to the specific normalizer (e.g. default_region).

    Returns:
        Normalized value, or original value if no normalizer exists.
    """
    if field_name == "skills" and isinstance(value, list):
        # If skills are already SkillEntry objects (from merge), don't re-normalize
        from pydantic import BaseModel
        if value and isinstance(value[0], BaseModel):
            return value
        return normalize_skills(value)

    normalizer = FIELD_NORMALIZERS.get(field_name)
    if normalizer:
        return normalizer(value, **kwargs) if kwargs else normalizer(value)

    return value
