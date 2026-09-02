"""Export current DB state to CSV:
- data/exports/bar_radar_venues.csv   (all venues, any menu_status)
- data/exports/bar_radar_valid.csv    (menu_status = VALID_MENU only)
- data/manual_review/manual_review.csv

Usage:
    python -m src.pipeline.export
"""
from __future__ import annotations

import csv
from pathlib import Path

from db.database import export_rows, export_menu_sources_rows, manual_review_rows
from db.migrate import run as run_migrations
from src.utils.config import REPO_ROOT
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

EXPORT_COLUMNS = [
    "venue_id", "venue_name", "city", "address", "website_url",
    "menu_url", "menu_source_type", "tier", "discovery_source",
    "menu_status", "last_checked_at",
]


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row[c] for c in columns})
    log.info("Wrote %d rows to %s", len(rows), path)


def run() -> None:
    # Self-migrating: export.py queries Phase 2 columns (menu_name,
    # extraction_status, etc.) regardless of which workflow calls it, so it
    # must never assume some other step already ran migrations first.
    run_migrations()
    rows = [dict(r) for r in export_rows()]

    all_path = REPO_ROOT / "data/exports/bar_radar_venues.csv"
    _write_csv(all_path, rows, EXPORT_COLUMNS)

    valid_rows = [r for r in rows if r.get("menu_status") == "VALID_MENU"]
    valid_path = REPO_ROOT / "data/exports/bar_radar_valid.csv"
    _write_csv(valid_path, valid_rows, EXPORT_COLUMNS)

    review_rows = [dict(r) for r in manual_review_rows()]
    review_path = REPO_ROOT / "data/manual_review/manual_review.csv"
    review_columns = ["review_id", "stage", "reason", "created_at", "venue_id", "venue_name", "city", "address", "website_url"]
    _write_csv(review_path, review_rows, review_columns)

    # Phase 2: every discovered menu source (multiple per venue), regardless
    # of extraction outcome - lets you see exactly what was found and how
    # each one turned out.
    menu_rows = [dict(r) for r in export_menu_sources_rows()]
    menu_columns = [
        "menu_source_id", "venue_id", "venue_name", "city", "menu_url",
        "menu_name", "menu_category", "menu_source_type", "is_primary",
        "discovery_method", "retrieval_method", "raw_file_path",
        "extraction_status", "extraction_confidence", "content_hash", "checked_at",
    ]
    menu_path = REPO_ROOT / "data/exports/bar_radar_menu_sources.csv"
    _write_csv(menu_path, menu_rows, menu_columns)

    extracted_rows = [
        r for r in menu_rows
        if r.get("extraction_status") in ("EXTRACTED", "PDF_OCR", "SCREENSHOT_OCR")
    ]
    extracted_path = REPO_ROOT / "data/exports/bar_radar_menu_sources_extracted.csv"
    _write_csv(extracted_path, extracted_rows, menu_columns)

    log.info(
        "Export summary: %d total venues, %d VALID_MENU, %d in manual review, "
        "%d menu sources discovered (%d successfully extracted)",
        len(rows), len(valid_rows), len(review_rows), len(menu_rows), len(extracted_rows),
    )


if __name__ == "__main__":
    run()
