"""Batch 2: for venues with status=NEW, find + validate their website and
menu, and write results back to SQLite. Designed to run in batches of
50-100 (see config/settings.yaml -> batching.enrichment_batch_size) so a
GitHub Actions job stays well within its time budget.

Usage:
    python -m src.pipeline.run_enrichment
    python -m src.pipeline.run_enrichment --batch-size 50
"""
from __future__ import annotations

import argparse
import uuid

from db.database import (
    add_manual_review,
    count_valid_menus_for_city,
    get_venues_needing_enrichment,
    init_db,
    requeue_venues_for_recheck,
    update_venue,
    upsert_menu_source,
)
from src.enrichment.menu_crawler import crawl_for_menu_candidates
from src.enrichment.menu_validator import pick_best_valid_menu
from src.enrichment.website_finder import find_website, verify_website_reachable
from src.utils.config import cities_config, settings
from src.utils.logging_utils import get_logger

log = get_logger(__name__)


def _city_quota(city: str) -> int:
    cfg = cities_config()
    for block in ("tier_1", "tier_2", "tier_3"):
        if city in cfg[block]["cities"]:
            return cfg[block]["cities"][city]
    return 10_000  # unknown city: no cap


def enrich_venue(venue) -> None:
    venue = dict(venue)
    venue_id = venue["venue_id"]

    # --- Step 1: website ---------------------------------------------------
    website_url, website_status = find_website(venue)
    if website_url and website_status == "FOUND":
        website_status = verify_website_reachable(website_url)

    update_venue(venue_id, {
        "website_url": website_url,
        "website_status": website_status,
        "status": "ENRICHED",
    })

    if website_status != "FOUND" or not website_url:
        upsert_menu_source({
            "menu_source_id": str(uuid.uuid4()),
            "venue_id": venue_id,
            "menu_url": None,
            "menu_source_type": "NONE",
            "menu_status": "WEBSITE_UNAVAILABLE" if website_status != "BLOCKED" else "BLOCKED",
            "menu_confidence": 0.0,
        })
        add_manual_review(str(uuid.uuid4()), venue_id, "WEBSITE", f"website_status={website_status}")
        return

    # --- Step 2: crawl for menu candidates ---------------------------------
    candidates = crawl_for_menu_candidates(website_url)
    if not candidates:
        upsert_menu_source({
            "menu_source_id": str(uuid.uuid4()),
            "venue_id": venue_id,
            "menu_url": None,
            "menu_source_type": "NONE",
            "menu_status": "NO_MENU_FOUND",
            "menu_confidence": 0.0,
        })
        add_manual_review(str(uuid.uuid4()), venue_id, "MENU", "No menu-like links found on site")
        return

    # --- Step 3: validate best candidate ------------------------------------
    result = pick_best_valid_menu(candidates)
    if result is None:
        upsert_menu_source({
            "menu_source_id": str(uuid.uuid4()),
            "venue_id": venue_id,
            "menu_url": candidates[0]["url"],
            "menu_source_type": candidates[0]["source_type"],
            "menu_status": "NO_MENU_FOUND",
            "menu_confidence": 0.0,
        })
        add_manual_review(str(uuid.uuid4()), venue_id, "MENU", "Candidates found but none validated")
        return

    upsert_menu_source({
        "menu_source_id": str(uuid.uuid4()),
        "venue_id": venue_id,
        **result,
    })
    if result["menu_status"] == "POSSIBLE_MENU":
        add_manual_review(str(uuid.uuid4()), venue_id, "MENU", f"Low confidence ({result['menu_confidence']})")


def run(batch_size: int | None = None, recheck: bool = False, recheck_city: str | None = None) -> None:
    init_db()
    if recheck:
        n = requeue_venues_for_recheck(city=recheck_city)
        log.info(
            "Recheck: requeued %d venue(s)%s for re-enrichment with current crawler/validator logic",
            n, f" in {recheck_city}" if recheck_city else "",
        )

    batch_size = batch_size or settings()["batching"]["enrichment_batch_size"]
    venues = get_venues_needing_enrichment(batch_size)
    log.info("Enriching %d venues (batch_size=%d)", len(venues), batch_size)

    processed = 0
    for venue in venues:
        quota = _city_quota(venue["city"])
        if count_valid_menus_for_city(venue["city"]) >= quota:
            # City already satisfied - mark as enriched/skip without spending
            # requests on it. It stays in the DB but out of the active queue.
            update_venue(venue["venue_id"], {"status": "REJECTED"})
            log.info("%s (%s) skipped - city quota already met", venue["venue_name"], venue["city"])
            continue

        try:
            enrich_venue(venue)
        except Exception as exc:  # noqa: BLE001 - keep batch alive on per-venue errors
            log.exception("Failed enriching venue %s: %s", venue["venue_id"], exc)
            update_venue(venue["venue_id"], {"status": "ENRICHED"})
            add_manual_review(str(uuid.uuid4()), venue["venue_id"], "MENU", f"Exception: {exc}")
        processed += 1

    log.info("Enrichment batch complete. %d venues processed.", processed)


def main():
    parser = argparse.ArgumentParser(description="BAR RADAR enrichment batch runner")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--recheck", action="store_true",
        help="Requeue already-enriched venues currently at NO_MENU_FOUND, POSSIBLE_MENU, "
             "WEBSITE_UNAVAILABLE, or BLOCKED for re-processing with the current crawler/"
             "validator logic, before running the normal batch.",
    )
    parser.add_argument(
        "--recheck-city", type=str, default=None,
        help="Limit --recheck to a single city (e.g. Berlin) instead of all cities.",
    )
    args = parser.parse_args()
    run(batch_size=args.batch_size, recheck=args.recheck, recheck_city=args.recheck_city)


if __name__ == "__main__":
    main()
