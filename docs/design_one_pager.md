# Design One-Pager: Candidate Data ETL Pipeline

## Problem

Recruiters and talent teams maintain candidate data across multiple disconnected systems — CSV spreadsheets, ATS platforms, GitHub profiles, resumes, and recruiter notes. Each source has different schemas, quality levels, and coverage. We need a pipeline that ingests all of these, resolves which records belong to the same candidate, normalizes data into a canonical representation, scores our confidence in each data point, and outputs a configurable view.

## Architecture

```
┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐   ┌──────────┐
│  Detect  │──▶│ Extract  │──▶│ Normalize │──▶│  Merge   │──▶│ Confidence │──▶│ Project  │──▶│ Validate │
│(file→type)│  │(→RawField)│  │(pure fns) │  │(cluster+ │  │ (scoring)  │  │(config→  │  │(schema)  │
└──────────┘   └──────────┘   └───────────┘   │ resolve  │  └────────────┘  │ reshape) │  └──────────┘
                                               │ flat→nest)│                  └──────────┘
                                               └──────────┘
```

Each stage is a **pure transformation** — no shared mutable state. The pipeline is composable: any stage can be run independently for testing or debugging.

## Key Design Decisions

### 1. Canonical Record as Source of Truth (Nested Complex Schema)

The `CandidateProfile` (Pydantic v2 model) is the single canonical representation. It uses a **nested complex schema** matching the assignment's exact output format:

- `emails: List[str]` — multiple emails, deduplicated
- `phones: List[str]` — E.164 normalized
- `location: { city, region, country }` — LocationData nested object
- `links: { linkedin, github, portfolio, other[] }` — LinksData nested object
- `skills: [{ name, confidence, sources[] }]` — SkillEntry list with per-skill confidence
- `experience: [{ company, title, start, end, summary }]` — ExperienceEntry list
- `education: [{ institution, degree, field, end_year }]` — EducationEntry list

**Why this matters:** The assignment explicitly asks for "clean separation" between the internal data model and the runtime-configured output. By making the canonical record immutable and the config a projection lens, we guarantee that adding new output formats never risks data corruption.

### 2. Extractors Emit Flat Fields; Merge Transforms to Nested

Every extractor emits `RawField(field, value, source, source_id, extraction_method, timestamp)` objects with **flat** field names (e.g., `email`, `phone`, `current_title`). The **merge engine** is the single place that transforms flat fields into the nested `CandidateProfile`:

- `email` → `emails[]` (union, deduplicate)
- `phone` → `phones[]` (normalize E.164, union)
- `location` + `country` → `LocationData { city, region, country }`
- `linkedin_url` + `github_url` → `LinksData { linkedin, github }`
- `current_title` → `headline`
- `current_company` + `current_title` → `ExperienceEntry[]`
- `skills` (list merge) → `SkillEntry[] { name, confidence, sources[] }`

**Why this design:** Extractors stay simple and independently testable. The merge engine is the single adapter layer, making it easy to add new extractors without changing the nested schema.

### 3. Entity Resolution: Match Key Cascade

```
Email (exact, case-insensitive) → Phone (normalized E.164) → Fuzzy name+company (≥85)
```

Email is the strongest identity signal. Phone is a reliable fallback. Fuzzy name+company is a last resort — it requires both to match to avoid false merges. The threshold of 85 (rapidfuzz WRatio) handles common variations (typos, abbreviations, "Inc." vs "Corp.") while avoiding false positives.

### 4. Conflict Resolution: Source Priority + Recency

When multiple sources disagree on a field value:
1. **Source priority order:** `csv > ats_json > github > linkedin > resume > notes`
   - Rationale: Structured sources (CSV, ATS) are typically curated by recruiters. Unstructured sources (resumes, notes) are more likely stale or misinterpreted.
2. **Tiebreaker:** Most recent timestamp wins.

**Exceptions:**
- **Skills** are merged as the **union** across all sources (not pick-winner)
- **Emails** and **Phones** are also unioned and deduplicated

### 5. Confidence Scoring Formula

```
field_confidence = base_weight(source) × method_modifier + corroboration_bonus − conflict_penalty
```

| Component | Description | Values |
|-----------|-------------|--------|
| base_weight | Source reliability tier | csv=0.80, ats=0.85, github=0.70, linkedin=0.90, resume=0.60, notes=0.40 |
| method_modifier | Extraction method quality | structured=1.0, api=0.95, regex=0.80, heuristic=0.60 |
| corroboration_bonus | Multiple sources agree | +0.10 per extra source (cap: +0.30) |
| conflict_penalty | Sources disagree | −0.15 |

**Overall confidence** = weighted average of field scores, where identity fields (emails=2.0, full_name=1.5) weigh more than supplementary ones (links=0.4).

### 6. Normalization as Pure Functions

Each normalizer:
- Takes a raw value, returns a canonical value or `None`
- Never throws — returns `None` on unparseable input
- Has no side effects or shared state
- Is independently unit-testable

| Normalizer | Library | Raw Input | Canonical Output |
|------------|---------|-----------|-----------------|
| `normalize_phone` | `phonenumbers` | `(415) 555-0101` | `+14155550101` (E.164) |
| `normalize_country` | `pycountry` | `United States` / `USA` | `US` (ISO-3166) |
| `normalize_date` | `python-dateutil` | `Jan 2024` | `2024-01` (YYYY-MM) |
| `normalize_skill` | `rapidfuzz` | `js`, `k8s`, `postgres` | `JavaScript`, `Kubernetes`, `PostgreSQL` |
| `normalize_email` | built-in | `Alice@Example.COM` | `alice@example.com` |
| `normalize_name` | built-in | `alice  chen` | `Alice Chen` |

## Projection Engine

The config specifies:
- **Which fields** to include (`fields[]` with `path` and `from` dot-path)
- **Array indexing** (`emails[0]` → first email as string)
- **List mapping** (`skills[].name` → flat list of skill names)
- **Whether to normalize** each field (per-field and global toggle)
- **How to handle missing values** (`null` / `omit` / `error`)
- **Whether to include metadata** (`include_confidence`, `include_provenance`)

The projection engine reads from the canonical record and emits a reshaped dict. This is validated against a JSON schema dynamically generated from the config.

## Edge Cases (10 tested)

| # | Scenario | Handling |
|---|----------|----------|
| 1 | Source file missing | Pipeline continues; missing fields stay null |
| 2 | Same candidate, different name spellings | Merged via email; highest-priority name wins |
| 3 | Conflicting field values | Source priority picks winner; confidence drops by 0.15 |
| 4 | Phone without country code | Default to US via phonenumbers; null if still invalid |
| 5 | Garbage/malformed JSON | Catch error, skip source, log, continue |
| 6 | Unicode/CJK names | Preserved correctly |
| 7 | Empty/null skills | Treated as empty list, no crash |
| 8 | Three-way conflict | Highest-priority source wins |
| 9 | Skills from multiple sources | Union merge, not pick-winner |
| 10 | Same phone, different formats | Normalized E.164 matching |

## Explicit Scope-Outs

1. **Live LinkedIn scraping** — ToS risk; mocked as static JSON fixture. Production: LinkedIn Partner API.
2. **ML-based skill extraction** — Using canonical alias-map + fuzzy matching. Production: fine-tuned NER model.
3. **Horizontal scaling** — In-memory for thousands. For millions: shard by email hash, Apache Spark for merge.

## Technology Choices

| Choice | Rationale |
|--------|-----------|
| **Pydantic v2** | Type-safe nested schema with model serialization |
| **phonenumbers** | Google's libphonenumber — industry standard for E.164 |
| **pycountry** | ISO standards database for country normalization |
| **rapidfuzz** | Fastest fuzzy matching library (C++ backend, MIT) |
| **pdfplumber** | Better text extraction than PyPDF2 for structured resumes |
| **jsonschema** | Industry standard for validating projected output |
