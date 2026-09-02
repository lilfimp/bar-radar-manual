-- BAR RADAR — SQLite schema
-- Phase 1 tables are fully used now. Phase 2 tables exist as empty scaffolding
-- so the pipeline (VENUE -> MENU_SOURCE -> MENU_SNAPSHOT -> MENU_ITEM ->
-- BRAND_MENTION -> CHANGE_EVENT) can be extended without a migration.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- VENUE: one row per discovered, deduplicated bar
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS venues (
    venue_id            TEXT PRIMARY KEY,       -- stable hash of name+lat+lon
    venue_name          TEXT NOT NULL,
    city                TEXT NOT NULL,
    tier                INTEGER NOT NULL,        -- 1, 2, 3
    category            TEXT,                    -- cocktail_bar, hotel_bar, bar, restaurant_bar
    address             TEXT,
    latitude            REAL,
    longitude           REAL,
    osm_type            TEXT,                    -- node/way/relation
    osm_id              INTEGER,
    website_url         TEXT,
    website_status      TEXT DEFAULT 'UNKNOWN',  -- UNKNOWN, FOUND, UNAVAILABLE, BLOCKED
    discovery_source    TEXT NOT NULL,            -- e.g. overpass_osm, duckduckgo_fallback
    discovery_query     TEXT,
    venue_confidence    REAL DEFAULT 0.0,         -- 0-1, is this really a drinks-led venue
    status              TEXT DEFAULT 'NEW',       -- NEW, ENRICHED, DUPLICATE, REJECTED, CLOSED
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_venues_city   ON venues(city);
CREATE INDEX IF NOT EXISTS idx_venues_status ON venues(status);
CREATE INDEX IF NOT EXISTS idx_venues_tier   ON venues(tier);

-- ---------------------------------------------------------------------------
-- MENU_SOURCE: candidate/confirmed menu location for a venue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS menu_sources (
    menu_source_id      TEXT PRIMARY KEY,
    venue_id            TEXT NOT NULL REFERENCES venues(venue_id),
    menu_url            TEXT,
    menu_source_type    TEXT,   -- HTML_PAGE, PDF, IMAGE, EXTERNAL_PLATFORM, SOCIAL, NONE
    menu_status         TEXT DEFAULT 'MANUAL_REVIEW',
        -- VALID_MENU, POSSIBLE_MENU, NO_MENU_FOUND, WEBSITE_UNAVAILABLE, BLOCKED, MANUAL_REVIEW
    menu_confidence      REAL DEFAULT 0.0,        -- 0-1
    discovered_at        TEXT DEFAULT (datetime('now')),
    last_checked_at      TEXT DEFAULT (datetime('now')),
    is_primary           INTEGER DEFAULT 1        -- best candidate for this venue
);

CREATE INDEX IF NOT EXISTS idx_menu_sources_venue  ON menu_sources(venue_id);
CREATE INDEX IF NOT EXISTS idx_menu_sources_status ON menu_sources(menu_status);

-- ---------------------------------------------------------------------------
-- Phase 2 scaffolding (unused by Phase 1 code, safe to leave empty)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS menu_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    menu_source_id       TEXT NOT NULL REFERENCES menu_sources(menu_source_id),
    captured_at          TEXT DEFAULT (datetime('now')),
    content_hash         TEXT,
    raw_content_path     TEXT,
    status               TEXT
);

CREATE TABLE IF NOT EXISTS menu_items (
    item_id              TEXT PRIMARY KEY,
    snapshot_id          TEXT NOT NULL REFERENCES menu_snapshots(snapshot_id),
    item_name            TEXT,
    item_category         TEXT,     -- cocktail, spirit, beer, wine, non_alcoholic
    price                TEXT,
    raw_text             TEXT
);

CREATE TABLE IF NOT EXISTS brand_mentions (
    mention_id            TEXT PRIMARY KEY,
    item_id               TEXT NOT NULL REFERENCES menu_items(item_id),
    brand_name            TEXT,
    confidence            REAL
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id               TEXT PRIMARY KEY,
    venue_id                TEXT NOT NULL REFERENCES venues(venue_id),
    event_type              TEXT,   -- MENU_CHANGED, WEBSITE_CHANGED, VENUE_CLOSED, ...
    detected_at              TEXT DEFAULT (datetime('now')),
    details                  TEXT
);

-- ---------------------------------------------------------------------------
-- Operational: manual review queue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS manual_review (
    review_id             TEXT PRIMARY KEY,
    venue_id               TEXT NOT NULL REFERENCES venues(venue_id),
    stage                  TEXT,   -- WEBSITE, MENU
    reason                 TEXT,
    created_at              TEXT DEFAULT (datetime('now')),
    resolved                INTEGER DEFAULT 0
);

-- Convenience view matching the required CSV export columns
CREATE VIEW IF NOT EXISTS v_export AS
SELECT
    v.venue_id,
    v.venue_name,
    v.city,
    v.address,
    v.website_url,
    m.menu_url,
    m.menu_source_type,
    v.tier,
    v.discovery_source,
    m.menu_status,
    m.last_checked_at
FROM venues v
LEFT JOIN menu_sources m
    ON m.venue_id = v.venue_id AND m.is_primary = 1
WHERE v.status != 'DUPLICATE' AND v.status != 'REJECTED';
