"""Thin SQLite access layer for BAR RADAR.

Deliberately not using an ORM - the schema is small and stable, and raw SQL
keeps the GitHub Actions runs fast and dependency-light.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.utils.config import db_path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()


@contextmanager
def get_conn():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Venue operations
# ---------------------------------------------------------------------------

def upsert_venue(venue: dict) -> bool:
    """Insert a venue if venue_id is new. Returns True if inserted, False if
    it already existed (i.e. a duplicate discovery hit, not an error)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT venue_id FROM venues WHERE venue_id = ?", (venue["venue_id"],)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """
            INSERT INTO venues (
                venue_id, venue_name, city, tier, category, address,
                latitude, longitude, osm_type, osm_id, website_url,
                website_status, discovery_source, discovery_query,
                venue_confidence, status
            ) VALUES (
                :venue_id, :venue_name, :city, :tier, :category, :address,
                :latitude, :longitude, :osm_type, :osm_id, :website_url,
                :website_status, :discovery_source, :discovery_query,
                :venue_confidence, :status
            )
            """,
            venue,
        )
        conn.commit()
        return True


def count_valid_menus_for_city(city: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM venues v
            JOIN menu_sources m ON m.venue_id = v.venue_id AND m.is_primary = 1
            WHERE v.city = ? AND m.menu_status = 'VALID_MENU'
            """,
            (city,),
        ).fetchone()
        return row["n"]


def count_candidates_for_city(city: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM venues WHERE city = ? AND status != 'DUPLICATE' AND status != 'REJECTED'",
            (city,),
        ).fetchone()
        return row["n"]


def get_venues_needing_enrichment(limit: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM venues WHERE status = 'NEW' ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()


def update_venue(venue_id: str, fields: dict) -> None:
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = "CURRENT_TIMESTAMP_PLACEHOLDER"
    set_clause = ", ".join(f"{k} = :{k}" for k in fields if k != "updated_at")
    set_clause += ", updated_at = datetime('now')"
    fields.pop("updated_at")
    fields["venue_id"] = venue_id
    with get_conn() as conn:
        conn.execute(f"UPDATE venues SET {set_clause} WHERE venue_id = :venue_id", fields)
        conn.commit()


def upsert_menu_source(menu_source: dict) -> None:
    with get_conn() as conn:
        # one primary menu_source per venue: replace if exists
        conn.execute(
            "DELETE FROM menu_sources WHERE venue_id = ? AND is_primary = 1",
            (menu_source["venue_id"],),
        )
        conn.execute(
            """
            INSERT INTO menu_sources (
                menu_source_id, venue_id, menu_url, menu_source_type,
                menu_status, menu_confidence, is_primary, last_checked_at
            ) VALUES (
                :menu_source_id, :venue_id, :menu_url, :menu_source_type,
                :menu_status, :menu_confidence, 1, datetime('now')
            )
            """,
            menu_source,
        )
        conn.commit()


def add_manual_review(review_id: str, venue_id: str, stage: str, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO manual_review (review_id, venue_id, stage, reason) VALUES (?, ?, ?, ?)",
            (review_id, venue_id, stage, reason),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Phase 2: multi-menu extraction operations
# ---------------------------------------------------------------------------

def get_extraction_candidates(limit: int) -> list[sqlite3.Row]:
    """Venues whose Phase 1 result gave a real menu to start from, that
    haven't been through Phase 2 discovery yet (no non-primary menu_sources
    rows recorded, i.e. we haven't run multi-menu discovery on them)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT v.venue_id, v.venue_name, v.city, v.address, v.website_url,
                   m.menu_url AS primary_menu_url, m.menu_source_type AS primary_menu_source_type
            FROM venues v
            JOIN menu_sources m ON m.venue_id = v.venue_id AND m.is_primary = 1
            WHERE m.menu_status IN ('VALID_MENU', 'POSSIBLE_MENU')
              AND v.venue_id NOT IN (
                  SELECT DISTINCT venue_id FROM menu_sources WHERE discovery_method IS NOT NULL
              )
            ORDER BY v.created_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def menu_source_exists(venue_id: str, menu_url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT menu_source_id FROM menu_sources WHERE venue_id = ? AND menu_url = ?",
            (venue_id, menu_url),
        ).fetchone()
        return row is not None


def get_menu_source_by_url(venue_id: str, menu_url: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM menu_sources WHERE venue_id = ? AND menu_url = ?",
            (venue_id, menu_url),
        ).fetchone()


def insert_menu_source_if_new(menu_source: dict) -> bool:
    """Non-destructive insert for Phase 2: unlike upsert_menu_source (Phase 1,
    which replaces the single primary row), this NEVER deletes or overwrites
    an existing row. Returns True if inserted, False if a row for this
    (venue_id, menu_url) already exists - the caller should treat that as
    'already known, do not re-create' and go update it instead if needed."""
    if menu_source_exists(menu_source["venue_id"], menu_source.get("menu_url")):
        return False
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO menu_sources (
                menu_source_id, venue_id, menu_url, menu_name, menu_category,
                menu_source_type, menu_status, menu_confidence, is_primary,
                discovery_method, retrieval_method, raw_file_path,
                extracted_text, extraction_status, extraction_confidence,
                content_hash, checked_at, discovered_at, last_checked_at
            ) VALUES (
                :menu_source_id, :venue_id, :menu_url, :menu_name, :menu_category,
                :menu_source_type, :menu_status, :menu_confidence, :is_primary,
                :discovery_method, :retrieval_method, :raw_file_path,
                :extracted_text, :extraction_status, :extraction_confidence,
                :content_hash, :checked_at, datetime('now'), datetime('now')
            )
            """,
            menu_source,
        )
        conn.commit()
        return True


def update_menu_source_extraction(menu_source_id: str, fields: dict) -> None:
    if not fields:
        return
    fields = dict(fields)
    fields["menu_source_id"] = menu_source_id
    fields["checked_at"] = "PLACEHOLDER"
    set_clause = ", ".join(f"{k} = :{k}" for k in fields if k not in ("menu_source_id", "checked_at"))
    set_clause += ", checked_at = datetime('now'), last_checked_at = datetime('now')"
    fields.pop("checked_at")
    with get_conn() as conn:
        conn.execute(
            f"UPDATE menu_sources SET {set_clause} WHERE menu_source_id = :menu_source_id",
            fields,
        )
        conn.commit()


def get_pending_extractions(limit: int) -> list[sqlite3.Row]:
    """menu_sources rows enrolled in Phase 2 (discovery_method set) that
    haven't been attempted yet. Deliberately PENDING-only, not "anything not
    yet successful": once a source has been attempted, its outcome (even
    FAILED/BLOCKED/PARTIAL) is terminal for automatic batch runs, so a
    permanently-broken site isn't re-crawled every single batch forever.
    Use requeue_for_retry() to explicitly re-attempt failed sources."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM menu_sources
            WHERE discovery_method IS NOT NULL
              AND extraction_status = 'PENDING'
            ORDER BY discovered_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def enroll_existing_menu_source_for_phase2(venue_id: str, menu_url: str, discovery_method: str) -> bool:
    """A candidate URL discovered by Phase 2 sometimes already exists as a
    Phase 1 row (most commonly: the venue's already-validated primary menu).
    insert_menu_source_if_new() correctly refuses to touch that row's
    content - but without this, that row would never enter the Phase 2
    extraction queue at all (its discovery_method stays NULL forever,
    since Phase 1 never set one). This attaches just enough Phase 2
    metadata to make it eligible for extraction, without altering any
    Phase 1 field. Returns True if the row was newly enrolled, False if it
    was already enrolled or doesn't exist."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT discovery_method FROM menu_sources WHERE venue_id = ? AND menu_url = ?",
            (venue_id, menu_url),
        ).fetchone()
        if row is None or row["discovery_method"] is not None:
            return False
        conn.execute(
            """
            UPDATE menu_sources
            SET discovery_method = ?, extraction_status = 'PENDING'
            WHERE venue_id = ? AND menu_url = ?
            """,
            (discovery_method, venue_id, menu_url),
        )
        conn.commit()
        return True


def requeue_for_retry(statuses: tuple[str, ...] = ("FAILED", "BLOCKED"), limit: int = 200) -> int:
    """Explicitly re-queue sources stuck in a non-terminal-by-choice status
    back to PENDING so the next extraction stage will retry them. Call this
    deliberately (e.g. via --retry-failed) rather than having it happen
    automatically every batch."""
    placeholders = ",".join("?" for _ in statuses)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT menu_source_id FROM menu_sources WHERE extraction_status IN ({placeholders}) LIMIT ?",
            (*statuses, limit),
        ).fetchall()
        ids = [r["menu_source_id"] for r in rows]
        if ids:
            id_placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE menu_sources SET extraction_status = 'PENDING' WHERE menu_source_id IN ({id_placeholders})",
                ids,
            )
            conn.commit()
        return len(ids)


def get_all_menu_sources_for_venue(venue_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM menu_sources WHERE venue_id = ? ORDER BY is_primary DESC, menu_category",
            (venue_id,),
        ).fetchall()


def add_to_retry_queue(retry_id: str, venue_id: str, reason: str) -> None:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT retry_id FROM extraction_retry_queue WHERE venue_id = ? AND resolved = 0",
            (venue_id,),
        ).fetchone()
        if existing:
            return
        conn.execute(
            "INSERT INTO extraction_retry_queue (retry_id, venue_id, reason) VALUES (?, ?, ?)",
            (retry_id, venue_id, reason),
        )
        conn.commit()


def export_menu_sources_rows() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT ms.menu_source_id, ms.venue_id, v.venue_name, v.city,
                   ms.menu_url, ms.menu_name, ms.menu_category, ms.menu_source_type,
                   ms.is_primary, ms.discovery_method, ms.retrieval_method,
                   ms.raw_file_path, ms.extraction_status, ms.extraction_confidence,
                   ms.content_hash, ms.checked_at
            FROM menu_sources ms
            JOIN venues v ON v.venue_id = ms.venue_id
            WHERE ms.discovery_method IS NOT NULL
            ORDER BY v.city, v.venue_name, ms.is_primary DESC, ms.menu_category
            """
        ).fetchall()


# ---------------------------------------------------------------------------
# Phase 1 recheck: reprocess venues whose enrichment result predates a
# crawler/validator fix, without re-running discovery.
# ---------------------------------------------------------------------------

DEFAULT_RECHECK_STATUSES = ("NO_MENU_FOUND", "POSSIBLE_MENU", "WEBSITE_UNAVAILABLE", "BLOCKED")


def requeue_venues_for_recheck(
    statuses: tuple[str, ...] = DEFAULT_RECHECK_STATUSES,
    city: str | None = None,
) -> int:
    """Resets venues.status back to 'NEW' for already-enriched venues whose
    primary menu_status is in `statuses`, so the next enrichment run picks
    them up again through the normal NEW-venue path - reusing website_url
    where it's already known (find_website() short-circuits on an existing
    website_url) and re-attempting website discovery where it wasn't found.
    Also resolves their old MENU-stage manual_review entries so the review
    queue doesn't accumulate stale duplicates. Returns the number requeued."""
    placeholders = ",".join("?" for _ in statuses)
    params: list = list(statuses)
    city_clause = ""
    if city:
        city_clause = "AND v.city = ?"
        params.append(city)

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT v.venue_id
            FROM venues v
            JOIN menu_sources m ON m.venue_id = v.venue_id AND m.is_primary = 1
            WHERE v.status = 'ENRICHED'
              AND m.menu_status IN ({placeholders})
              {city_clause}
            """,
            params,
        ).fetchall()
        venue_ids = [r["venue_id"] for r in rows]
        if not venue_ids:
            return 0

        id_placeholders = ",".join("?" for _ in venue_ids)
        conn.execute(
            f"UPDATE venues SET status = 'NEW' WHERE venue_id IN ({id_placeholders})",
            venue_ids,
        )
        conn.execute(
            f"""
            UPDATE manual_review SET resolved = 1
            WHERE stage = 'MENU' AND resolved = 0 AND venue_id IN ({id_placeholders})
            """,
            venue_ids,
        )
        conn.commit()
        return len(venue_ids)


def export_rows() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM v_export ORDER BY tier, city, venue_name").fetchall()


def manual_review_rows() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT r.review_id, r.stage, r.reason, r.created_at,
                   v.venue_id, v.venue_name, v.city, v.address, v.website_url
            FROM manual_review r
            JOIN venues v ON v.venue_id = r.venue_id
            WHERE r.resolved = 0
            ORDER BY r.created_at
            """
        ).fetchall()
