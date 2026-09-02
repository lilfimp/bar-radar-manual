"""Discover bar venues in a German city from OpenStreetMap via the Overpass API.

Free, no API key, generous rate limits for polite usage. This is the primary
discovery source. We query for amenity=bar / amenity=pub, plus anything with
a cocktail-related cuisine tag, inside the administrative boundary of the
given city so we don't need manual bounding boxes.
"""
from __future__ import annotations

import hashlib
import time
from typing import Iterable

from src.utils.config import settings
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Overpass matches OSM's administrative boundary by its exact `name` tag,
# and OSM stores German cities under their German names. Our city config
# (config/cities.yaml) uses the common English names people actually type,
# so we translate here at query-build time only - the venue's stored `city`
# field stays whatever config/cities.yaml uses, so quotas keep matching.
CITY_OSM_NAME_ALIASES = {
    "Munich": "München",
    "Cologne": "Köln",
    "Nuremberg": "Nürnberg",
    "Hanover": "Hannover",
    "Frankfurt": "Frankfurt am Main",
    "Freiburg": "Freiburg im Breisgau",
}

# The Overpass query itself declares a 60s server-side execution budget, so
# the HTTP client needs a longer timeout than the default used elsewhere
# (e.g. website fetches) or slow/busy responses look like failures.
OVERPASS_HTTP_TIMEOUT_SECONDS = 90

# The free public Overpass instance enforces a per-IP concurrent/sequential
# query-slot limit. Several large admin-boundary queries back-to-back in the
# same run (e.g. querying 5 big German cities in a row) can trip it, coming
# back as 429/504 - which looked identical to "no results" before this fix.
# A short cooldown between cities plus a couple of backed-off retries on a
# rate-limit response is enough to get through a normal discovery batch
# without hammering the shared instance.
OVERPASS_INTER_CITY_DELAY_SECONDS = 5
OVERPASS_RATE_LIMIT_RETRIES = 2
OVERPASS_RATE_LIMIT_BACKOFF_SECONDS = 20


def _venue_id(name: str, lat: float, lon: float) -> str:
    key = f"{name.strip().lower()}|{round(lat, 4)}|{round(lon, 4)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _build_query(city: str) -> str:
    """Overpass QL: resolve the city as an area, then find bar-like venues
    inside it. `area["name"="City"]["boundary"="administrative"]` restricts
    to the administrative area so we don't pick up e.g. villages of the same
    name elsewhere."""
    osm_name = CITY_OSM_NAME_ALIASES.get(city, city)
    return f"""
    [out:json][timeout:60];
    area["name"="{osm_name}"]["boundary"="administrative"]["admin_level"~"^(4|6|8)$"]->.searchArea;
    (
      node["amenity"~"^(bar|pub)$"](area.searchArea);
      way["amenity"~"^(bar|pub)$"](area.searchArea);
      node["cuisine"~"cocktail", i](area.searchArea);
      way["cuisine"~"cocktail", i](area.searchArea);
    );
    out center tags;
    """


def _passes_name_filter(name: str) -> bool:
    if not name:
        return False
    filters = settings()["osm_venue_filters"]["exclude_name_contains"]
    lowered = name.lower()
    return not any(bad in lowered for bad in filters)


def _classify_category(tags: dict) -> str:
    name = (tags.get("name") or "").lower()
    cuisine = (tags.get("cuisine") or "").lower()
    amenity = tags.get("amenity", "")
    if "cocktail" in cuisine or "cocktail" in name:
        return "cocktail_bar"
    if "hotel" in name:
        return "hotel_bar"
    if amenity == "pub":
        return "bar"  # keep taxonomy simple/flat for Phase 1
    return "bar"


def _query_overpass_with_retry(query: str, city: str):
    """A 429/504 from Overpass means "rate limited, back off" - retrying
    immediately just gets rate limited again. Waits a real cooldown between
    attempts, distinct from http_utils' generic short retry delay."""
    for attempt in range(OVERPASS_RATE_LIMIT_RETRIES + 1):
        resp = get(OVERPASS_URL, params={"data": query}, timeout=OVERPASS_HTTP_TIMEOUT_SECONDS, max_retries=0)
        if resp is not None and resp.status_code == 200:
            return resp
        status = resp.status_code if resp is not None else "no response"
        if resp is not None and resp.status_code not in (429, 504):
            return resp  # a real error, not a rate limit - no point retrying
        if attempt < OVERPASS_RATE_LIMIT_RETRIES:
            log.warning(
                "Overpass rate-limited for %s (%s) - waiting %ds before retry %d/%d",
                city, status, OVERPASS_RATE_LIMIT_BACKOFF_SECONDS, attempt + 1, OVERPASS_RATE_LIMIT_RETRIES,
            )
            time.sleep(OVERPASS_RATE_LIMIT_BACKOFF_SECONDS)
    return resp


def discover_city(city: str, tier: int, max_candidates: int) -> list[dict]:
    """Query Overpass for a single city and return a list of venue dicts
    ready for db.database.upsert_venue(). Does not deduplicate across cities
    or write to the DB - see discovery/dedupe.py and pipeline/run_discovery.py."""
    query = _build_query(city)
    resp = _query_overpass_with_retry(query, city)
    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else "no response"
        log.error("Overpass query failed for %s (%s)", city, status)
        return []

    try:
        elements = resp.json().get("elements", [])
    except ValueError:
        log.error("Overpass returned non-JSON for %s", city)
        return []

    venues = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not _passes_name_filter(name):
            continue

        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue

        address_parts = [
            tags.get("addr:street", ""),
            tags.get("addr:housenumber", ""),
        ]
        address = " ".join(p for p in address_parts if p).strip()
        if tags.get("addr:postcode") or tags.get("addr:city"):
            address += f", {tags.get('addr:postcode', '')} {tags.get('addr:city', city)}".strip()

        venues.append(
            {
                "venue_id": _venue_id(name, lat, lon),
                "venue_name": name,
                "city": city,
                "tier": tier,
                "category": _classify_category(tags),
                "address": address or None,
                "latitude": lat,
                "longitude": lon,
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "website_url": tags.get("website") or tags.get("contact:website"),
                "website_status": "FOUND" if tags.get("website") else "UNKNOWN",
                "discovery_source": "overpass_osm",
                "discovery_query": f"amenity=bar/pub OR cuisine=cocktail in {city}",
                "venue_confidence": 0.9 if tags.get("amenity") in ("bar", "pub") else 0.7,
                "status": "NEW",
            }
        )
        if len(venues) >= max_candidates:
            break

    log.info("Discovered %d candidate venues in %s", len(venues), city)
    return venues


def discover_cities(city_tier_quota: Iterable[tuple[str, int, int]]) -> list[dict]:
    """city_tier_quota: iterable of (city, tier, max_candidates)."""
    all_venues: list[dict] = []
    city_list = list(city_tier_quota)
    for i, (city, tier, max_candidates) in enumerate(city_list):
        all_venues.extend(discover_city(city, tier, max_candidates))
        if i < len(city_list) - 1:
            time.sleep(OVERPASS_INTER_CITY_DELAY_SECONDS)
    return all_venues
