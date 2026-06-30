"""
CLI entry point — wires the full ETL pipeline together.

Pipeline: detect → extract → normalize → merge → confidence → project → validate

Usage:
  python -m src.cli --inputs sample_inputs/ --config config/example_config.json --output out.json
  python -m src.cli --inputs sample_inputs/ --config config/example_config.json --output out.json --explain
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

import click

from src.detect import SourceType, get_all_source_files
from src.schema import RawField
from src.extract.csv_extractor import CsvExtractor
from src.extract.ats_json import AtsJsonExtractor
from src.extract.github import GithubExtractor
from src.extract.resume import ResumeExtractor
from src.extract.notes import NotesExtractor
from src.merge import merge_all
from src.confidence import compute_confidence
from src.project import project_all
from src.validate import validate_canonical, validate_all_projected

logger = logging.getLogger("candidate_etl")

# Map source types to their extractors
EXTRACTOR_MAP = {
    SourceType.CSV: CsvExtractor,
    SourceType.ATS_JSON: AtsJsonExtractor,
    SourceType.LINKEDIN_JSON: AtsJsonExtractor,   # LinkedIn fixture uses same parser
    SourceType.GITHUB: GithubExtractor,
    SourceType.RESUME_PDF: ResumeExtractor,
    SourceType.RESUME_DOCX: ResumeExtractor,
    SourceType.NOTES_TXT: NotesExtractor,
}


def setup_logging(verbose: bool) -> None:
    """Configure logging for the pipeline."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _generate_explain_report(profiles, all_records, source_files, config_data) -> str:
    """
    Generate a human-readable explanation of how the pipeline
    processed the data — useful for demos and debugging.
    """
    lines = []
    lines.append("=" * 70)
    lines.append("PIPELINE EXPLANATION REPORT")
    lines.append("=" * 70)
    lines.append("")

    # --- Source summary ---
    lines.append("1. SOURCES DETECTED")
    lines.append("-" * 40)
    source_counts = {}
    for _, st in source_files:
        source_counts[st.value] = source_counts.get(st.value, 0) + 1
    for stype, count in source_counts.items():
        lines.append(f"   - {stype}: {count} file(s)")
    lines.append(f"   Total: {len(source_files)} source files")
    lines.append("")

    # --- Extraction summary ---
    lines.append("2. EXTRACTION RESULTS")
    lines.append("-" * 40)
    lines.append(f"   Raw candidate records extracted: {len(all_records)}")
    all_fields_count = sum(len(fields) for fields in all_records.values())
    lines.append(f"   Total raw field values: {all_fields_count}")
    lines.append("")

    # --- Merge/dedup summary ---
    lines.append("3. ENTITY RESOLUTION (MERGE)")
    lines.append("-" * 40)
    lines.append(f"   Input records: {len(all_records)}")
    lines.append(f"   Output profiles: {len(profiles)}")
    lines.append(f"   Deduplication ratio: {len(all_records)} -> {len(profiles)} "
                 f"({len(all_records) - len(profiles)} duplicates merged)")
    lines.append("")

    # --- Per-candidate details ---
    lines.append("4. CANDIDATE PROFILES")
    lines.append("-" * 40)
    for i, p in enumerate(profiles):
        name = p.full_name or "Unknown"
        email = p.emails[0] if p.emails else "no email"
        sources = set()
        for prov in p.provenance:
            sources.add(prov.source)
        source_list = ", ".join(sorted(sources))
        conflict_count = sum(1 for fc in p.field_confidences if fc.has_conflict)

        lines.append(f"   [{i+1}] {name} <{email}>")
        lines.append(f"       Sources: {source_list}")
        lines.append(f"       Overall confidence: {p.overall_confidence:.3f}")
        lines.append(f"       Fields with conflicts: {conflict_count}")

        # Show conflicts in detail
        for fc in p.field_confidences:
            if fc.has_conflict:
                # Find the different values from provenance
                field_provs = [pv for pv in p.provenance if pv.field == fc.field]
                values = list(set(str(pv.raw_value) for pv in field_provs))
                if len(values) > 1:
                    lines.append(f"         >> {fc.field}: {len(values)} different values from {fc.source_count} sources")
                    for pv in field_provs[:3]:  # show first 3
                        lines.append(f"            -> {pv.source} ({pv.method.value}): {str(pv.raw_value)[:60]}")

        lines.append("")

    # --- Confidence summary ---
    lines.append("5. CONFIDENCE SUMMARY")
    lines.append("-" * 40)
    if profiles:
        avg_conf = sum(p.overall_confidence for p in profiles) / len(profiles)
        min_conf = min(p.overall_confidence for p in profiles)
        max_conf = max(p.overall_confidence for p in profiles)
        lines.append(f"   Average overall confidence: {avg_conf:.3f}")
        lines.append(f"   Range: {min_conf:.3f} - {max_conf:.3f}")
    lines.append("")

    # --- Config summary ---
    lines.append("6. PROJECTION CONFIG")
    lines.append("-" * 40)
    field_names = [f.get("name") or f.get("path", "unknown") for f in config_data.get("fields", [])]
    lines.append(f"   Fields projected: {len(field_names)}")
    lines.append(f"   Fields: {', '.join(field_names)}")
    lines.append(f"   Include confidence: {config_data.get('include_confidence', False)}")
    lines.append(f"   Include provenance: {config_data.get('include_provenance', False)}")
    lines.append(f"   Missing field policy: {config_data.get('on_missing', 'null')}")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


@click.command()
@click.option(
    "--inputs", "-i",
    required=True,
    type=click.Path(exists=True),
    help="Path to input directory containing source files.",
)
@click.option(
    "--config", "-c",
    required=True,
    type=click.Path(exists=True),
    help="Path to runtime projection config JSON.",
)
@click.option(
    "--output", "-o",
    default="out.json",
    type=click.Path(),
    help="Path for output JSON file.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (debug) logging.",
)
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help="Print a human-readable explanation report after pipeline runs.",
)
def main(inputs: str, config: str, output: str, verbose: bool, explain: bool) -> None:
    """
    Candidate Data ETL Pipeline.

    Ingests data from multiple sources, normalizes, merges, scores,
    and projects configurable output views.
    """
    setup_logging(verbose)
    logger.info("=" * 60)
    logger.info("Candidate Data ETL Pipeline")
    logger.info("=" * 60)

    # --- Load config ---
    config_path = Path(config)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        logger.info("Loaded config from %s", config_path)
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        sys.exit(1)

    # --- Phase 1: Detect sources ---
    logger.info("-" * 40)
    logger.info("Phase 1: Detecting sources...")
    source_files = get_all_source_files(inputs)
    if not source_files:
        logger.error("No source files found in %s", inputs)
        sys.exit(1)
    logger.info("Found %d source files", len(source_files))

    # --- Phase 2: Extract ---
    logger.info("-" * 40)
    logger.info("Phase 2: Extracting data...")
    all_records: Dict[str, List[RawField]] = {}

    for file_path, source_type in source_files:
        extractor_cls = EXTRACTOR_MAP.get(source_type)
        if extractor_cls is None:
            logger.warning("No extractor for source type: %s", source_type)
            continue

        try:
            extractor = extractor_cls()
            records = extractor.extract(file_path)
            # Merge into global records dict (keys may collide — that's intentional
            # for the merge step to handle)
            for candidate_key, fields in records.items():
                if candidate_key in all_records:
                    all_records[candidate_key].extend(fields)
                else:
                    all_records[candidate_key] = fields
            logger.info("  ✓ %s (%s): %d candidates", file_path.name, source_type.value, len(records))
        except Exception as e:
            logger.error("  ✗ %s: extraction failed: %s", file_path.name, e)
            continue

    logger.info("Total raw records: %d", len(all_records))

    # --- Phase 3: Merge (includes normalization) ---
    logger.info("-" * 40)
    logger.info("Phase 3: Merging and resolving...")
    profiles = merge_all(all_records, apply_normalization=True)
    logger.info("Merged into %d canonical profiles", len(profiles))

    # --- Phase 4: Confidence scoring ---
    logger.info("-" * 40)
    logger.info("Phase 4: Computing confidence scores...")
    for profile in profiles:
        compute_confidence(profile)

    # --- Phase 5: Validate canonical ---
    logger.info("-" * 40)
    logger.info("Phase 5: Validating canonical profiles...")
    for i, profile in enumerate(profiles):
        vr = validate_canonical(profile)
        if not vr.is_valid:
            logger.warning("Canonical validation issues for profile %d: %s", i, vr.errors)

    # --- Phase 6: Project ---
    logger.info("-" * 40)
    logger.info("Phase 6: Projecting output...")
    projected = project_all(profiles, config_data)

    # --- Phase 7: Validate projected output ---
    logger.info("-" * 40)
    logger.info("Phase 7: Validating projected output...")
    validation_results = validate_all_projected(projected, config_data)
    valid_count = sum(1 for vr in validation_results if vr.is_valid)
    logger.info("Valid: %d/%d", valid_count, len(validation_results))

    # --- Write output ---
    output_path = Path(output)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(projected, f, indent=2, ensure_ascii=False, default=str)
        logger.info("=" * 60)
        logger.info("Output written to %s (%d records)", output_path, len(projected))
        logger.info("=" * 60)
    except Exception as e:
        logger.error("Failed to write output: %s", e)
        sys.exit(1)

    # --- Explain mode ---
    if explain:
        report = _generate_explain_report(profiles, all_records, source_files, config_data)
        click.echo("")
        click.echo(report)

        # Also write report to file
        explain_path = output_path.with_suffix(".explain.txt")
        with open(explain_path, "w", encoding="utf-8") as f:
            f.write(report)
        click.echo(f"\nExplanation report also saved to: {explain_path}")


if __name__ == "__main__":
    main()
