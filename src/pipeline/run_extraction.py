"""Batch 3 (Phase 2): for venues with a validated Phase 1 menu, discover ALL
relevant menu links (cocktail, wine, food, happy hour, etc - not just the
one Phase 1 found), then extract text from every one of them.

Two resumable stages, run every invocation:
  A. Discovery  - venues not yet scanned for multi-menu links (checked via
                  "does this venue have any menu_sources row with
                  discovery_method set?") get scanned once. Every found
                  link becomes its own PENDING menu_sources row. Already-
                  scanned venues are skipped automatically - discovery only
                  runs once per venue unless you clear its rows manually.
  B. Extraction - any menu_sources row not yet at a terminal success status
                  (EXTRACTED / PDF_OCR / SCREENSHOT_OCR) gets processed.
                  Successfully extracted rows are never reprocessed on
                  later runs (see database.get_pending_extractions).

Usage:
    python -m src.pipeline.run_extraction
    python -m src.pipeline.run_extraction --discovery-batch-size 50 --extraction-batch-size 150
"""
from __future__ import annotations

import argparse
import uuid

from db.database import (
    add_to_retry_queue,
    enroll_existing_menu_source_for_phase2,
    get_extraction_candidates,
    get_pending_extractions,
    insert_menu_source_if_new,
    requeue_for_retry,
    update_menu_source_extraction,
)
from db.migrate import run as run_migrations
from src.extraction.discovery import discover_all_menu_links, normalize_url
from src.extraction.extractor import extract_menu_source
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

DEFAULT_DISCOVERY_BATCH = 50
DEFAULT_EXTRACTION_BATCH = 150


def run_discovery_stage(batch_size: int) -> int:
    venues = get_extraction_candidates(batch_size)
    log.info("Discovery stage: scanning %d venues for all menu links", len(venues))

    total_new_sources = 0
    for venue in venues:
        venue = dict(venue)
        candidates = discover_all_menu_links(venue["website_url"], venue["primary_menu_url"])

        if not candidates:
            add_to_retry_queue(str(uuid.uuid4()), venue["venue_id"], "No menu links found in Phase 2 discovery")
            log.info("%s: no menu links found, added to retry queue", venue["venue_name"])
            continue

        inserted_for_venue = 0
        for candidate in candidates:
            is_primary_candidate = normalize_url(candidate["url"]) == normalize_url(venue["primary_menu_url"] or "")
            row = {
                "menu_source_id": str(uuid.uuid4()),
                "venue_id": venue["venue_id"],
                "menu_url": candidate["url"],
                "menu_name": candidate.get("link_text") or None,
                "menu_category": "OTHER",  # refined during extraction once text is available
                "menu_source_type": candidate["source_type"],
                "menu_status": "MANUAL_REVIEW",  # Phase 1 status field; Phase 2 uses extraction_status instead
                "menu_confidence": 0.0,
                "is_primary": 1 if is_primary_candidate else 0,
                "discovery_method": "phase2_multi_menu_crawl",
                "retrieval_method": None,
                "raw_file_path": None,
                "extracted_text": None,
                "extraction_status": "PENDING",
                "extraction_confidence": 0.0,
                "content_hash": None,
                "checked_at": None,
            }
            if insert_menu_source_if_new(row):
                inserted_for_venue += 1
            else:
                # URL already exists as a row (most commonly: the Phase 1
                # primary menu). It must still enter the Phase 2 extraction
                # queue - just without touching its existing content.
                enroll_existing_menu_source_for_phase2(
                    venue["venue_id"], candidate["url"], "phase2_multi_menu_crawl"
                )

        total_new_sources += inserted_for_venue
        log.info("%s: %d menu source(s) discovered", venue["venue_name"], inserted_for_venue)

    log.info("Discovery stage complete: %d new menu sources across %d venues", total_new_sources, len(venues))
    return total_new_sources


def run_extraction_stage(batch_size: int) -> int:
    pending = get_pending_extractions(batch_size)
    log.info("Extraction stage: processing %d pending menu sources", len(pending))

    processed = 0
    for source in pending:
        source = dict(source)
        candidate = {
            "url": source["menu_url"],
            "source_type": source["menu_source_type"],
            "link_text": source.get("menu_name") or "",
        }
        try:
            result = extract_menu_source(candidate, source["venue_id"])
        except Exception as exc:  # noqa: BLE001 - keep the batch alive on per-source errors
            log.exception("Extraction failed for %s: %s", source["menu_url"], exc)
            result = {
                "extraction_status": "FAILED",
                "extraction_confidence": 0.0,
                "retrieval_method": None,
                "raw_file_path": None,
                "content_hash": None,
                "extracted_text": None,
                "menu_category": source.get("menu_category") or "OTHER",
                "menu_name": source.get("menu_name"),
            }

        update_menu_source_extraction(source["menu_source_id"], result)
        processed += 1

    log.info("Extraction stage complete: %d menu sources processed", processed)
    return processed


def run(discovery_batch_size: int, extraction_batch_size: int, retry_failed: bool) -> None:
    run_migrations()
    if retry_failed:
        n = requeue_for_retry()
        log.info("Re-queued %d FAILED/BLOCKED sources for retry", n)
    run_discovery_stage(discovery_batch_size)
    run_extraction_stage(extraction_batch_size)


def main():
    parser = argparse.ArgumentParser(description="BAR RADAR Phase 2 - menu extraction batch runner")
    parser.add_argument("--discovery-batch-size", type=int, default=DEFAULT_DISCOVERY_BATCH)
    parser.add_argument("--extraction-batch-size", type=int, default=DEFAULT_EXTRACTION_BATCH)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-queue sources stuck at FAILED/BLOCKED before running the batch, "
             "instead of leaving them terminal until explicitly retried.",
    )
    args = parser.parse_args()
    run(args.discovery_batch_size, args.extraction_batch_size, args.retry_failed)


if __name__ == "__main__":
    main()
