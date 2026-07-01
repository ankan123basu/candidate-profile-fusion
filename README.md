# Multi-Source Candidate Data Transformer

> **Eightfold Engineering Intern Assignment (Jul-Dec 2026)**
> Built by **Ankan Basu** | ankanbasu10@gmail.com

A production-quality ETL pipeline that ingests candidate data from **multiple heterogeneous sources** (structured + unstructured), normalizes formats, merges duplicate candidates via entity resolution, scores confidence in each data point, and projects configurable output views — all driven by a runtime config with **zero code changes**.

## Architecture

```
Ingest → Parse → Cleanse → Resolve → Score → Reshape → Certify
```

**Key design principle:** The canonical `CandidateProfile` is the single source of truth with a **nested complex schema** (lists of objects, nested dicts). The projection config is a **pure read-only lens** — it never mutates the canonical record.

## Quick Start

### 1. Set Up Environment

```bash
cd EightfoldAIAssignment

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate       # Windows
# source venv/bin/activate    # macOS/Linux

# Install dependencies
pip install -e ".[dev]"
```

### 2. Run the Pipeline (Full Default Output)

```bash
python -m src.cli --inputs sample_inputs/ --config config/default_config.json --output output/out_default.json --explain
```

### 3. Run with Custom Config (Remapped Fields + Confidence)

```bash
python -m src.cli --inputs sample_inputs/ --config config/custom_config.json --output output/out_custom.json
```

### 4. Run with Minimal Config (Omit Missing, Provenance Enabled)

```bash
python -m src.cli --inputs sample_inputs/ --config config/minimal_config.json --output output/out_minimal.json
```

### 5. Run Tests (116 tests — all passing)

```bash
pytest tests/ -v --tb=short
```

---

## Pipeline Stages

| Stage | Module | What It Does | Key Design Choice |
|-------|--------|-------------|-------------------|
| **Ingest** | `src/detect.py` | Routes each file to the right parser | Content-aware: inspects JSON key structure to distinguish ATS from GitHub from LinkedIn |
| **Parse** | `src/extract/` | Per-source extractors emit `RawField` tuples | Emit **flat fields** only — never build nested objects, independently testable |
| **Cleanse** | `src/normalize.py` | Pure functions: phone→E.164, country→ISO-3166, skill→taxonomy, date→YYYY-MM | Returns `None` on unparseable input — **never throws**, never guesses |
| **Resolve** | `src/merge.py` | Entity resolution (email→phone→fuzzy cascade) + flat-to-nested build | **Single adapter layer**: only place flat fields become the nested `CandidateProfile` |
| **Score** | `src/confidence.py` | Per-field confidence = source_weight × method + corroboration − conflict | Documented formula, not a black box |
| **Reshape** | `src/project.py` | Config-driven projection: field selection, path remapping, array mapping | Config is a **read-only lens** — supports `null`/`omit`/`error` for missing values |
| **Certify** | `src/validate.py` | JSON-schema validation of canonical record AND projected output | Two-layer: Pydantic (internal) + jsonschema (output conformance) |

---

## Supported Sources

### Structured Sources
| Source | File Type | Extractor | Method |
|--------|-----------|-----------|--------|
| Recruiter CSV | `.csv` | `CsvExtractor` | Structured parse |
| ATS JSON Export | `.json` | `AtsJsonExtractor` | Structured parse |

### Unstructured Sources
| Source | File Type | Extractor | Method |
|--------|-----------|-----------|--------|
| LinkedIn | `.json` (fixture) | `AtsJsonExtractor` | Static mock (ToS compliance) |
| GitHub | `.json` (usernames) | `GithubExtractor` | **Real GitHub REST API** — fetches profile + repos + languages |
| Resume (text) | `.pdf`, `.docx` | `ResumeExtractor` | pdfplumber / python-docx + regex + heuristic |
| Resume (scanned) | `.pdf` | `ResumeExtractor` | **OCR fallback** via pytesseract (optional) |
| Recruiter Notes | `.txt` | `NotesExtractor` | Regex + heuristic pattern matching |

### GitHub API Integration

The pipeline calls the **real GitHub REST API** to extract candidate data:

```json
[
  {"username": "octocat"},
  {"username": "torvalds"}
]
```

Set `GITHUB_TOKEN` env var for higher rate limits (5000/hr vs 60/hr).

### OCR Support (Optional)

For scanned PDF resumes, the pipeline automatically falls back to OCR:

```bash
pip install pytesseract
# Also requires Tesseract OCR engine installed on system PATH
```

If OCR libraries aren't installed, the pipeline gracefully degrades.

---

## Canonical Profile Schema

The canonical record uses a **nested complex schema** matching the assignment's default output format:

| Field | Type | Normalization |
|-------|------|---------------|
| `candidate_id` | `string` (UUID) | Auto-generated |
| `full_name` | `string` | Title case |
| `emails` | `string[]` | Lowercase, deduplicated |
| `phones` | `string[]` | E.164 via phonenumbers, deduplicated |
| `location` | `{ city, region, country }` | Country → ISO-3166 alpha-2 |
| `links` | `{ linkedin, github, portfolio, other[] }` | — |
| `headline` | `string` | From current_title/position |
| `years_experience` | `float` | — |
| `skills` | `[{ name, confidence, sources[] }]` | Canonical taxonomy via rapidfuzz |
| `experience` | `[{ company, title, start, end, summary }]` | Dates → YYYY-MM |
| `education` | `[{ institution, degree, field, end_year }]` | — |
| `provenance` | `[{ field, source, method, raw_value }]` | Auto-tracked |
| `overall_confidence` | `float [0,1]` | Weighted average |

---

## Runtime Config

The projection config controls the output shape. It supports **field remapping, array indexing, and list mapping**:

```json
{
  "fields": [
    {"path": "full_name", "type": "string"},
    {"path": "primary_email", "from": "emails[0]", "type": "string"},
    {"path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164"},
    {"path": "skill_names", "from": "skills[].name", "type": "string[]"}
  ],
  "include_confidence": true,
  "include_provenance": false,
  "on_missing": "null"
}
```

| Config Key | Values | Description |
|-----------|--------|-------------|
| `fields[].path` or `fields[].name` | string | Output field name |
| `fields[].from` | string | Canonical field to read from (supports `[0]` index, `[].sub` map) |
| `fields[].normalize` | bool/string | Override normalization for this field |
| `include_confidence` | bool | Include `_confidence` object in output |
| `include_provenance` | bool | Include `_provenance` array in output |
| `on_missing` | `null`/`omit`/`error` | How to handle missing fields |

### Provided Configs

| Config | Description |
|--------|-------------|
| `default_config.json` | Full schema output — all canonical fields + provenance + confidence |
| `custom_config.json` | Remapped fields (`emails[0]` → `primary_email`) + confidence scores |
| `minimal_config.json` | 5 fields only, `on_missing: "omit"`, provenance enabled |

---

## Sample Output

### Default Config — full canonical output

```json
{
  "candidate_id": "424fa947-e2de-4f5b-b9b6-3afbb00a865b",
  "full_name": "Alice Chen",
  "emails": ["alice.chen@example.com"],
  "phones": ["+14155550101"],
  "location": { "city": "San Francisco", "region": "CA", "country": "US" },
  "links": { "linkedin": "https://linkedin.com/in/alicechen", "github": "https://github.com/alicechen" },
  "headline": "Senior Software Engineer",
  "years_experience": 8.0,
  "skills": [
    { "name": "Python", "confidence": 1.0, "sources": ["ats_json", "csv", "notes", "resume"] },
    { "name": "Django", "confidence": 0.95, "sources": ["ats_json", "csv", "resume"] },
    { "name": "React", "confidence": 0.95, "sources": ["ats_json", "csv", "resume"] },
    { "name": "PostgreSQL", "confidence": 0.95, "sources": ["ats_json", "csv", "resume"] }
  ],
  "experience": [{ "company": "Acme Corp", "title": "Senior Software Engineer" }],
  "education": [
    { "institution": "Stanford University", "degree": "M.S. Computer Science" },
    { "institution": "UC Berkeley", "degree": "B.S. Computer Science" }
  ],
  "provenance": [
    { "field": "full_name", "source": "csv", "method": "structured_parse", "raw_value": "Alice Chen" },
    { "field": "full_name", "source": "ats_json", "method": "structured_parse", "raw_value": "Alice Chen" }
  ],
  "overall_confidence": 0.946
}
```

### Custom Config — remapped fields (proves projection decoupling)

Using the **exact example config from the assignment** (`config/custom_config.json`), the same canonical data is projected into a completely different shape:

```json
{
  "full_name": "Alice Chen",
  "primary_email": "alice.chen@example.com",
  "phone": "+14155550101",
  "skills": ["Python", "Django", "React", "PostgreSQL", "AWS", "Docker", "Kubernetes"],
  "_confidence": {
    "overall": 0.946,
    "fields": {
      "full_name": { "score": 1.0, "source_count": 4, "has_conflict": false },
      "emails": { "score": 1.0, "source_count": 4, "has_conflict": true },
      "headline": { "score": 0.85, "source_count": 3, "has_conflict": true }
    }
  }
}
```

Note: `primary_email` is remapped from `emails[0]`, `skills` from `skills[].name` — **same engine, no code changes, just a different config file.**

---

## Data Normalization (6 normalizers)

All normalizers are **pure functions** — no side effects, return `None` on unparseable input, independently unit-testable.

| Field | Library | Before (raw) | After (normalized) |
|-------|---------|-------------|--------------------|
| Phone | `phonenumbers` | `(415) 555-0101` | `+14155550101` (E.164) |
| Country | `pycountry` | `United States` / `USA` / `US` | `US` (ISO-3166 alpha-2) |
| Date | `python-dateutil` | `Jan 2024` / `2024-01-15` | `2024-01` (YYYY-MM) |
| Skills | `rapidfuzz` | `js` / `k8s` / `postgres` | `JavaScript` / `Kubernetes` / `PostgreSQL` |
| Email | built-in | `Alice@Example.COM` | `alice@example.com` |
| Name | built-in | `alice  chen` | `Alice Chen` (title case) |

---

## Confidence Scoring

```
field_confidence = base_weight(source) × method_modifier + corroboration_bonus − conflict_penalty
```

| Parameter | Values |
|-----------|--------|
| **Source weights** | csv=0.80, ats_json=0.85, github=0.70, linkedin=0.90, resume=0.60, notes=0.40 |
| **Method modifiers** | structured_parse=1.0, api=0.95, regex=0.80, heuristic=0.60 |
| **Corroboration bonus** | +0.10 per additional agreeing source (capped at +0.30) |
| **Conflict penalty** | −0.15 when sources disagree |
| **Overall confidence** | Weighted average by field importance (emails=2.0, full_name=1.5, skills=1.3, ...) |

---

## Merge / Entity Resolution

**Match cascade** (strongest signal first):
1. **Email** — exact match (case-insensitive)
2. **Phone** — normalized E.164 exact match
3. **Fuzzy name + company** — rapidfuzz WRatio ≥ 85

**Conflict resolution:** Source priority order (`csv > ats_json > github > linkedin > resume > notes`), with most-recent-wins as tiebreaker.

**Skills** are merged as the **union** across all sources (not pick-winner).

---

## Edge Cases Handled (10 total — all tested)

| # | Scenario | Handling | Test |
|---|----------|----------|------|
| 1 | **Missing source file** | Pipeline continues, unaffected fields stay null | `test_edge_cases.py::TestEdgeCase1` |
| 2 | **Duplicate candidate, different name spellings** | Merged via email match; CSV name wins | `test_edge_cases.py::TestEdgeCase2` |
| 3 | **Conflicting field values across sources** | Source priority picks winner; confidence drops by 0.15 | `test_edge_cases.py::TestEdgeCase3` |
| 4 | **Phone without country code** | Defaults to US region via phonenumbers; null if invalid | `test_edge_cases.py::TestEdgeCase4` |
| 5 | **Malformed/garbage ATS JSON** | Catches JSONDecodeError, skips bad records, logs error | `test_edge_cases.py::TestEdgeCase5` |
| 6 | **Unicode/international names (CJK, accents)** | Preserved correctly through normalization | `test_edge_cases.py::TestEdgeCase6` |
| 7 | **Empty/null skills list** | Treated as no skills, doesn't crash | `test_edge_cases.py::TestEdgeCase7` |
| 8 | **Three-way field conflict** | Highest-priority source wins; conflict tracked | `test_edge_cases.py::TestEdgeCase8` |
| 9 | **Skills union across sources** | All unique skills merged, not pick-winner | `test_edge_cases.py::TestEdgeCase9` |
| 10 | **Phone formats merge (E.164)** | Different formats normalized, then matched | `test_edge_cases.py::TestEdgeCase10` |

---

## Explicit Scope-Outs

- **Live LinkedIn scraping** — Mocked as static JSON fixture (LinkedIn ToS compliance). Production: use LinkedIn Partner API.
- **ML-based skill extraction** — Uses canonical alias-map + fuzzy matching instead. Production: fine-tuned NER model.
- **Horizontal scaling** — In-memory processing handles thousands; for millions: shard by email hash with Apache Spark.

---

## Project Structure

```
├── pyproject.toml              # PEP 621 metadata + dependencies
├── README.md
├── docs/
│   └── design_one_pager.md     # Architecture & design decisions
├── src/
│   ├── cli.py                  # Click-based CLI (7-phase pipeline)
│   ├── detect.py               # Auto-detect source type
│   ├── schema.py               # Pydantic v2 canonical models (nested)
│   ├── normalize.py            # Pure normalizer functions
│   ├── merge.py                # Entity resolution + flat→nested transform
│   ├── confidence.py           # Scoring engine
│   ├── project.py              # Config-driven projection (array indexing)
│   ├── validate.py             # JSON-schema validation
│   └── extract/
│       ├── base.py             # BaseExtractor abstract class
│       ├── csv_extractor.py    # CSV parser
│       ├── ats_json.py         # ATS JSON parser
│       ├── github.py           # Real GitHub REST API
│       ├── resume.py           # PDF/DOCX + OCR fallback
│       └── notes.py            # Free-text notes parser
├── config/
│   ├── default_config.json     # Full schema output
│   ├── custom_config.json      # Remapped + confidence
│   └── minimal_config.json     # Minimal fields + provenance
├── sample_inputs/              # 7 sample data files
├── output/                     # Pipeline output JSONs
│   ├── out_default.json
│   ├── out_custom.json
│   ├── out_minimal.json
│   └── out_default.explain.txt
└── tests/                      # 116 pytest tests (all passing)
    ├── test_edge_cases.py      # 10 edge case scenarios
    ├── test_extractors.py      # Per-extractor tests
    ├── test_merge.py           # Clustering + resolution
    ├── test_normalize.py       # Normalizer unit tests
    ├── test_project.py         # Projection + remapping
    ├── test_confidence.py      # Scoring logic
    └── test_validate.py        # Validation rules
```

## Dependencies

| Package | Purpose |
|---------|---------|
| pydantic v2 | Type-safe schema with nested models + validation |
| phonenumbers | Phone → E.164 normalization (Google's libphonenumber) |
| pycountry | Country → ISO-3166 alpha-2 |
| python-dateutil | Date → YYYY-MM parsing |
| pdfplumber | PDF text extraction |
| python-docx | DOCX text extraction |
| rapidfuzz | Fuzzy matching (entity resolution + skill normalization) |
| requests | GitHub REST API |
| jsonschema | Projected output validation |
| click | CLI framework |
| pytest | Test framework |

## Optional Environment Variable

| Variable | Purpose | Default |
|----------|---------|---------|
| `GITHUB_TOKEN` | GitHub API token for higher rate limits (5000/hr vs 60/hr) | None (unauthenticated) |
