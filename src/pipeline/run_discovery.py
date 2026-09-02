"""Batch 1: discover candidate venues for a slice of cities and load them
into SQLite (deduplicated, status=NEW).

Usage:
    python -m src.pipeline.run_discovery
    python -m src.pipeline.run_discovery --cities Berlin Hamburg
    python -m src.pipeline.run_discovery --limit 3   # first N cities not yet at quota

Stops discovering for a city once it already has enough VALID_MENU venues
(quota reached) - see should_skip_city().
"""
from __future__ import annotations

import argparse
import time

from db.database import count_candidates_for_city, count_valid_menus_for_city, init_db, upsert_venue
from src.discovery.dedupe import dedupe_venues
from src.discovery.overpass_source import discover_city, OVERPASS_INTER_CITY_DELAY_SECONDS
from src.utils.config import cities_config
from src.utils.logging_utils import get_logger

log = get_logger(__name__)


def all_city_quota_tuples() -> list[tuple[str, int, int]]:
    """Flatten config/cities.yaml into (city, tier, quota) tuples."""
    cfg = cities_config()
    tier_map = {"tier_1": 1, "tier_2": 2, "tier_3": 3}
    out = []
    for tier_key, tier_num in tier_map.items():
        block = cfg[tier_key]
        for city, quota in block["cities"].items():
            out.append((city, tier_num, quota, block["candidate_multiplier"]))
    return out


def should_skip_city(city: str, quota: int) -> bool:
    """Skip discovery once the city already has enough validated menus, OR
    already has enough raw candidates to expect quota to be met after
    enrichment (avoids over-discovering)."""
    if count_valid_menus_for_city(city) >= quota:
        return True
    return False


def run(target_cities: list[str] | None = None, limit: int | None = None) -> None:
    init_db()
    all_tuples = all_city_quota_tuples()

    if target_cities:
        all_tuples = [t for t in all_tuples if t[0] in target_cities]
    if limit:
        all_tuples = all_tuples[:limit]

    total_inserted = 0
    for i, (city, tier, quota, multiplier) in enumerate(all_tuples):
        if should_skip_city(city, quota):
            log.info("Skipping %s - quota of %d valid menus already reached", city, quota)
            continue

        existing_candidates = count_candidates_for_city(city)
        target_candidates = int(quota * multiplier)
        remaining = max(target_candidates - existing_candidates, 0)
        if remaining == 0:
            log.info("Skipping %s - already have %d candidates (target %d)", city, existing_candidates, target_candidates)
            continue

        raw_venues = discover_city(city, tier, remaining)
        deduped = dedupe_venues(raw_venues)

        inserted = 0
        for v in deduped:
            if upsert_venue(v):
                inserted += 1
        total_inserted += inserted
        log.info("%s: %d discovered, %d new after dedupe/upsert", city, len(raw_venues), inserted)

        # Cooldown before the next city's Overpass query - several large
        # admin-boundary queries back-to-back can trip the free public
        # instance's per-IP rate limit, which otherwise looks identical to
        # "this city has no bars" (see overpass_source.py for the retry-with-
        # backoff on an actual 429/504 response).
        if i < len(all_tuples) - 1:
            time.sleep(OVERPASS_INTER_CITY_DELAY_SECONDS)

    log.info("Discovery run complete. %d new venues inserted.", total_inserted)


def main():
    parser = argparse.ArgumentParser(description="BAR RADAR discovery batch runner")
    parser.add_argument("--cities", nargs="*", help="Specific cities to run, default: all not-yet-at-quota")
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N cities (from config order)")
    args = parser.parse_args()
    run(target_cities=args.cities, limit=args.limit)


if __name__ == "__main__":
    main()
