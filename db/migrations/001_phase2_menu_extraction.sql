-- Migration 001: Phase 2 - menu extraction engine
--
-- Extends menu_sources so it can hold MANY rows per venue (one per distinct
-- menu: cocktail, wine, food, happy hour, etc.) instead of a single
-- "primary" slot. All new columns are additive (ALTER TABLE ADD COLUMN) so
-- existing Phase 1 rows and data are untouched - they simply get NULL/
-- default values for the new columns until Phase 2 processes them.
--
-- Note on SQLite: ADD COLUMN only accepts constant defaults (no
-- datetime('now')), so checked_at is set explicitly by the application
-- code rather than via a column default.

ALTER TABLE menu_sources ADD COLUMN menu_name TEXT;
ALTER TABLE menu_sources ADD COLUMN menu_category TEXT DEFAULT 'OTHER';
ALTER TABLE menu_sources ADD COLUMN discovery_method TEXT;
ALTER TABLE menu_sources ADD COLUMN retrieval_method TEXT;
ALTER TABLE menu_sources ADD COLUMN raw_file_path TEXT;
ALTER TABLE menu_sources ADD COLUMN extracted_text TEXT;
ALTER TABLE menu_sources ADD COLUMN extraction_status TEXT DEFAULT 'PENDING';
ALTER TABLE menu_sources ADD COLUMN extraction_confidence REAL DEFAULT 0.0;
ALTER TABLE menu_sources ADD COLUMN content_hash TEXT;
ALTER TABLE menu_sources ADD COLUMN checked_at TEXT;

-- Prevent the same menu URL being inserted twice for the same venue.
-- SQLite treats multiple NULLs as distinct, so venues with menu_url IS NULL
-- (Phase 1 NONE/failed cases) are unaffected by this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_menu_sources_venue_url
    ON menu_sources(venue_id, menu_url);

CREATE INDEX IF NOT EXISTS idx_menu_sources_extraction_status
    ON menu_sources(extraction_status);

CREATE INDEX IF NOT EXISTS idx_menu_sources_category
    ON menu_sources(menu_category);

-- Venues whose Phase 1 result was NONE/failed, or whose extraction keeps
-- failing, get queued here for later manual or automated re-discovery
-- rather than being silently dropped.
CREATE TABLE IF NOT EXISTS extraction_retry_queue (
    retry_id     TEXT PRIMARY KEY,
    venue_id     TEXT NOT NULL REFERENCES venues(venue_id),
    reason       TEXT,
    added_at     TEXT DEFAULT (datetime('now')),
    resolved     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_retry_queue_resolved
    ON extraction_retry_queue(resolved);
