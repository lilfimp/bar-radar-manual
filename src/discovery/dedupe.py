"""Deduplicate discovered venues.

Two layers:
1. Exact venue_id collision (same normalized name + rounded lat/lon) - handled
   automatically by database.upsert_venue() returning False.
2. Fuzzy near-duplicates - same venue listed slightly differently (e.g. a
   node AND a way for the same building, or a name with/without "Bar").
   We catch these with a normalized-name + proximity check before insert.
"""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

NAME_NOISE_WORDS = {"bar", "the", "café", "cafe", "restaurant", "lounge", "&"}


def normalize_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s]", "", name)
    tokens = [t for t in name.split() if t not in NAME_NOISE_WORDS]
    return " ".join(tokens) or name


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def is_likely_duplicate(a: dict, b: dict, name_threshold: float = 0.82, distance_m: float = 60) -> bool:
    """True if venue dicts a and b are probably the same physical bar."""
    if a["city"] != b["city"]:
        return False
    name_sim = SequenceMatcher(None, normalize_name(a["venue_name"]), normalize_name(b["venue_name"])).ratio()
    if name_sim < name_threshold:
        return False
    if a.get("latitude") is None or b.get("latitude") is None:
        return name_sim > 0.95  # no coords: require near-exact name match
    dist = _haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    return dist <= distance_m


def dedupe_venues(venues: list[dict]) -> list[dict]:
    """Remove fuzzy duplicates, keeping the first occurrence. O(n^2) but n is
    a few thousand at most per run, which is fine."""
    kept: list[dict] = []
    for v in venues:
        if any(is_likely_duplicate(v, k) for k in kept):
            continue
        kept.append(v)
    return kept
