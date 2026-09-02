"""Import a hand-curated list of venues and their menu source URLs directly
into the database, bypassing Overpass discovery and the website-search step
entirely.

This is for venues you specifically want included - a competitor's bar, a
personal favorite, venues you already have menu URLs for - rather than
whatever Overpass happens to surface. Because you're supplying the URLs
yourself, there's no dependency on the (currently unreliable) DuckDuckGo/
Bing search fallback at all.

CSV shape: ONE ROW PER MENU SOURCE, not one row per venue. Rows sharing the
same (venue_name, city) are grouped into a single venue automatically - this
is how you attach multiple menus (drinks, wine, happy hour, a 4-page photo
menu, etc.) to one bar. See data/manual_import/venues_template.csv.

Columns:
    venue_name        required
    city              required
    tier              optional, default 1 (only needs to be set on one row per venue)
    address            optional (only needs to be set on one row per venue)
    website_url        optional (only needs to be set on one row per venue)
    menu_url            optional - if blank, this row just registers the venue
                        with no menu source (rare; usually every row has one)
    menu_name            optional label, e.g. "Cocktail Menu", "Wine List",
                          "Drinks Menu (2/4)" for a multi-image menu
    menu_category         optional - one of COCKTAIL, DRINKS, WINE, BEER,
                           SPIRITS, FOOD, HAPPY_HOUR, BRUNCH, SEASONAL,
                           ROOM_SERVICE, OTHER. Auto-detected from the URL/
                           menu_name if left blank.
    menu_source_type       optional - one of PDF, IMAGE, HTML_PAGE,
                            EXTERNAL_PLATFORM. Auto-detected from the URL's
                            file extension if left blank (.pdf -> PDF,
                            .jpg/.png/etc -> IMAGE, otherwise HTML_PAGE).
    is_primary               optional - "true"/"1"/"yes" for the ONE row that
                              is this venue's main/representative menu. If no
                              row marks this, the first menu row for that
                              venue is used as primary automatically.

Every menu row is created with discovery_method='manual_curated' and
extraction_status='PENDING' - exactly the state Phase 2's extraction batch
(src/pipeline/run_extraction.py) already looks for, so nothing extra is
needed to get real text/OCR out of these: just run the normal "BAR RADAR -
Menu Extraction Batch" workflow afterward. HTML/JS-rendered pages, PDFs
(text or scanned), and images all go through the same extractor dispatch
Phase 2 already has (see src/extraction/extractor.py) - no special handling
needed here regardless of the mix of link types you provide.

Usage:
    python -m src.pipeline.import_manual_venues
    python -m src.pipeline.import_manual_venues --file data/manual_import/my_list.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import uuid
from collections import defaultdict
from pathlib import Path

from db.database import get_conn, insert_menu_source_if_new, upsert_venue
from db.migrate import run as run_migrations
from src.discovery.dedupe import is_likely_duplicate
from src.enrichment.menu_crawler import _classify_link
from src.extraction.categorizer import classify as classify_menu_category
from src.utils.config import REPO_ROOT
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

DEFAULT_IMPORT_PATH = REPO_ROOT / "data/manual_import/venues_template.csv"

TRUE_VALUES = {"1", "true", "yes", "y"}


def _venue_id(name: str, city: str) -> str:
    key = f"{name.strip().lower()}|{city.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _find_possible_duplicate(name: str, city: str) -> str | None:
    """Checks existing venues in the same city for a fuzzy name match.
    Returns a human-readable warning string if one is found, else None."""
    candidate = {"venue_name": name, "city": city, "latitude": None, "longitude": None}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT venue_name FROM venues WHERE city = ? AND status != 'DUPLICATE'", (city,)
        ).fetchall()
    for row in rows:
        existing = {"venue_name": row["venue_name"], "city": city, "latitude": None, "longitude": None}
        if is_likely_duplicate(candidate, existing):
            return row["venue_name"]
    return None


def _group_rows_by_venue(rows: list[dict]) -> "list[tuple[tuple[str, str], list[dict]]]":
    """Groups CSV rows by (venue_name, city), preserving first-seen order -
    this is what lets multiple menu-source rows attach to one venue."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for row in rows:
        name = (row.get("venue_name") or "").strip()
        city = (row.get("city") or "").strip()
        if not name or not city:
            continue
        key = (name, city)
        if key not in grouped:
            order.append(key)
        grouped[key].append(row)
    return [(key, grouped[key]) for key in order]


def _pick_primary_row(venue_rows: list[dict]) -> dict | None:
    explicit = [r for r in venue_rows if (r.get("is_primary") or "").strip().lower() in TRUE_VALUES and (r.get("menu_url") or "").strip()]
    if explicit:
        return explicit[0]
    return next((r for r in venue_rows if (r.get("menu_url") or "").strip()), None)


def import_venue_group(name: str, city: str, venue_rows: list[dict]) -> dict:
    """Returns a summary dict: {status, venue_name, detail}."""
    first_row = venue_rows[0]
    tier = 1
    address = None
    website_url = None
    for row in venue_rows:
        tier = int(row["tier"]) if row.get("tier") else tier
        address = address or ((row.get("address") or "").strip() or None)
        website_url = website_url or ((row.get("website_url") or "").strip() or None)

    duplicate_of = _find_possible_duplicate(name, city)
    venue_id = _venue_id(name, city)
    has_menu_rows = any((r.get("menu_url") or "").strip() for r in venue_rows)

    venue = {
        "venue_id": venue_id,
        "venue_name": name,
        "city": city,
        "tier": tier,
        "category": "bar",
        "address": address,
        "latitude": None,
        "longitude": None,
        "osm_type": None,
        "osm_id": None,
        "website_url": website_url,
        "website_status": "FOUND" if website_url else "UNKNOWN",
        "discovery_source": "manual_curated",
        "discovery_query": None,
        "venue_confidence": 1.0,  # a human specifically chose this venue
        # ENRICHED (not NEW) when menu URLs are already supplied, so Phase 1's
        # enrichment batch doesn't try to search for a website we don't need
        # it to search for. Still NEW if this row only registers the venue
        # itself (no menu_url anywhere) so normal enrichment picks it up.
        "status": "ENRICHED" if has_menu_rows else "NEW",
    }
    venue_inserted = upsert_venue(venue)

    primary_row = _pick_primary_row(venue_rows)
    menu_sources_added = 0
    menu_sources_existing = 0

    for row in venue_rows:
        menu_url = (row.get("menu_url") or "").strip()
        if not menu_url:
            continue

        menu_name = (row.get("menu_name") or "").strip() or None
        menu_category = (row.get("menu_category") or "").strip().upper()
        if not menu_category:
            menu_category = classify_menu_category(url=menu_url, link_text=menu_name or "")
        source_type = (row.get("menu_source_type") or "").strip().upper()
        if not source_type:
            source_type = _classify_link(menu_url)

        inserted = insert_menu_source_if_new({
            "menu_source_id": str(uuid.uuid4()),
            "venue_id": venue_id,
            "menu_url": menu_url,
            "menu_name": menu_name or menu_category.title().replace("_", " "),
            "menu_category": menu_category,
            "menu_source_type": source_type,
            "menu_status": "MANUAL_REVIEW",  # Phase 1 field - set for real once extraction runs
            "menu_confidence": 0.0,
            "is_primary": 1 if row is primary_row else 0,
            "discovery_method": "manual_curated",
            "retrieval_method": None,
            "raw_file_path": None,
            "extracted_text": None,
            "extraction_status": "PENDING",
            "extraction_confidence": 0.0,
            "content_hash": None,
            "checked_at": None,
        })
        if inserted:
            menu_sources_added += 1
        else:
            menu_sources_existing += 1

    if not venue_inserted and menu_sources_added == 0 and menu_sources_existing == 0:
        return {"status": "ALREADY_EXISTS", "venue_name": name, "detail": f"venue_id {venue_id} already in DB, no new menu rows"}

    detail = f"city={city}, tier={tier}, {menu_sources_added} new menu source(s)"
    if menu_sources_existing:
        detail += f" ({menu_sources_existing} already existed, skipped)"
    if duplicate_of:
        detail += f" | WARNING: similar existing venue '{duplicate_of}' in {city} - check this isn't a duplicate"

    return {"status": "OK", "venue_name": name, "detail": detail}


def run(csv_path: Path | None = None) -> None:
    run_migrations()
    path = csv_path or DEFAULT_IMPORT_PATH
    if not path.exists():
        log.error("Import file not found: %s", path)
        return

    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = _group_rows_by_venue(rows)
    log.info("Importing %d venue(s) from %d row(s) in %s", len(groups), len(rows), path)

    results = [import_venue_group(name, city, venue_rows) for (name, city), venue_rows in groups]

    for r in results:
        log.info("[%s] %s - %s", r["status"], r["venue_name"], r["detail"])

    ok = [r for r in results if r["status"] == "OK"]
    existing = [r for r in results if r["status"] == "ALREADY_EXISTS"]
    log.info(
        "Import complete: %d venue(s) added/updated, %d already fully existed. "
        "Run the Menu Extraction Batch workflow next to extract the actual menu content.",
        len(ok), len(existing),
    )


def main():
    parser = argparse.ArgumentParser(description="BAR RADAR manual venue import")
    parser.add_argument("--file", type=str, default=None, help="Path to CSV file (default: data/manual_import/venues_template.csv)")
    args = parser.parse_args()
    run(csv_path=Path(args.file) if args.file else None)


if __name__ == "__main__":
    main()
