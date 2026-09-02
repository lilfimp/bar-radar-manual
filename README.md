# BAR RADAR — Venue Discovery & Menu Finder (Phase 1)

Builds a validated list of ~1,000 active German bar venues, each with a working
drinks-menu URL, at €0 recurring cost. Runs as scheduled batches via GitHub
Actions, writing to SQLite and exporting CSV.

## 1. Architecture

```
 ┌────────────┐   ┌──────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────┐
 │ Discovery  │→│ Dedupe   │→│ Website Find  │→│ Menu Crawl + │→│ Export │
 │ (Overpass) │   │          │   │ + Verify      │   │ Validate     │   │ CSV    │
 └────────────┘   └──────────┘   └───────────────┘   └──────────────┘   └────────┘
        │                                                     │
        └───────────────────── SQLite (single source of truth) ─────┘
```

- **Discovery** (`src/discovery/`): pulls candidate venues from OpenStreetMap
  via the Overpass API — free, no key, structured data (name, coords,
  address, sometimes website).
- **Dedupe**: fuzzy name + geo-proximity match removes near-duplicate OSM
  entries before they ever hit the DB.
- **Enrichment** (`src/enrichment/`): finds the venue's website (OSM tag,
  else a free DuckDuckGo HTML search fallback), crawls it for menu-looking
  links (HTML page / PDF / image / external platform), then fetches and
  scores the best candidate for actual drinks content.
- **SQLite** (`db/`): one file, no server, trivially portable, cheap to
  commit as CSV snapshots. Schema is Phase-2-ready (see §3).
- **Orchestration** (`src/pipeline/`): three CLI entrypoints — discovery
  batch, enrichment batch, CSV export — each idempotent and safe to re-run.
- **GitHub Actions** (`.github/workflows/`): discovery runs twice daily
  (cheap), enrichment runs every 4 hours in batches of ~75 (does the HTTP-
  heavy work), both commit updated CSV exports back to the repo.

No paid APIs, no proxies, no persistent server. Playwright is wired in as an
optional dependency only, for a future JS-heavy-site fallback — not required
for the baseline pipeline to work.

## 2. Free data sources

| Source | Use | Notes |
|---|---|---|
| **Overpass API** (OpenStreetMap) | Primary discovery: `amenity=bar/pub`, `cuisine~cocktail` inside each city's administrative boundary | No key, generous for polite/batched use, includes many `website` tags already |
| **DuckDuckGo HTML search** (`html.duckduckgo.com/html/`) | Fallback website lookup when OSM has no website tag | No key; used sparingly, only for venues missing a website |
| Venue's own website | Menu discovery (HTML links, PDFs, images, external menu platforms) | requests + BeautifulSoup; PDF text via `pypdf` |
| *(optional, not yet wired)* City tourism boards / Wikivoyage nightlife lists | Extra discovery coverage in Tier 2/3 cities where OSM density is thinner | Left as a hook — site-specific scrapers only pay off if OSM under-delivers for a given city |

## 3. Database schema

Phase 1 tables (used now) + Phase 2 scaffolding (empty, ready for the next
stage) — see `db/schema.sql` for the authoritative DDL.

```
venues            venue_id PK, venue_name, city, tier, category, address,
                  latitude, longitude, osm_type, osm_id, website_url,
                  website_status, discovery_source, discovery_query,
                  venue_confidence, status, created_at, updated_at

menu_sources      menu_source_id PK, venue_id FK, menu_url,
                  menu_source_type, menu_status, menu_confidence,
                  discovered_at, last_checked_at, is_primary

manual_review     review_id PK, venue_id FK, stage, reason, created_at, resolved

-- Phase 2 (empty scaffolding today):
menu_snapshots    snapshot_id PK, menu_source_id FK, captured_at,
                  content_hash, raw_content_path, status
menu_items        item_id PK, snapshot_id FK, item_name, item_category,
                  price, raw_text
brand_mentions    mention_id PK, item_id FK, brand_name, confidence
change_events     event_id PK, venue_id FK, event_type, detected_at, details
```

`v_export` is a SQL view joining `venues` + primary `menu_sources` into
exactly the columns the spec requires — `export.py` just selects from it.

## 4. Repo structure

```
bar-radar/
├── README.md
├── requirements.txt
├── .gitignore
├── config/
│   ├── cities.yaml          # tier/quota config (edit this to change targets)
│   └── settings.yaml        # keywords, thresholds, batch sizes, HTTP config
├── db/
│   ├── schema.sql
│   └── database.py          # all SQLite access goes through here
├── src/
│   ├── discovery/
│   │   ├── overpass_source.py
│   │   └── dedupe.py
│   ├── enrichment/
│   │   ├── website_finder.py
│   │   ├── menu_crawler.py
│   │   └── menu_validator.py
│   ├── pipeline/
│   │   ├── run_discovery.py   # CLI: batch 1
│   │   ├── run_enrichment.py  # CLI: batch 2
│   │   └── export.py          # CLI: CSV export
│   └── utils/
│       ├── config.py
│       ├── http_utils.py
│       └── logging_utils.py
├── data/
│   ├── bar_radar.db           # gitignored, generated locally / cached in CI
│   ├── exports/                # bar_radar_venues.csv, bar_radar_valid.csv
│   └── manual_review/          # manual_review.csv
├── tests/
│   ├── test_dedupe.py
│   └── test_menu_validator.py
└── .github/workflows/
    ├── discover.yml
    └── enrich.yml
```

## 5. Implementation plan

1. **Local bring-up** (this delivery): schema, discovery, dedupe, website
   finder, menu crawler/validator, orchestration CLIs, tests — all runnable
   locally in VS Code before touching CI.
2. **Local dry run**: `run_discovery --cities Berlin --limit 1` against a
   couple of cities, inspect `data/bar_radar.db` with a SQLite viewer,
   sanity-check candidate counts and address/category quality.
3. **Enrichment dry run**: small `--batch-size 10` run, manually spot-check
   5-10 `VALID_MENU` results and 5-10 `manual_review` entries to calibrate
   `confidence_thresholds` in `settings.yaml`.
4. **Scale up locally**: run full discovery across all cities once, then
   run enrichment repeatedly until per-city quotas are met or the manual
   review pile stabilizes.
5. **Wire up GitHub Actions**: push repo, add the two workflows, confirm a
   manual `workflow_dispatch` run completes and commits CSVs.
6. **Turn on schedules**: discovery 2x/day, enrichment every 4h; monitor
   Actions minutes usage and CSV growth for a few days.
7. **Manual review pass**: periodically review `manual_review.csv`
   (currently a CSV triage list — Phase 1 does not auto-resolve these).
8. **Phase 2 hook-in**: once venues.csv is stable at ~1,000 `VALID_MENU`
   rows, start populating `menu_snapshots`/`menu_items` by re-fetching each
   `menu_sources.menu_url` on a schedule and diffing against the last
   snapshot's `content_hash` → `change_events`.

## 6. Main technical risks

- **Overpass coverage gaps**: OSM bar density varies by city; some Tier 3
  cities may not hit their candidate multiplier from Overpass alone. Config
  has a hook for adding editorial/tourism-site scrapers per city if needed.
- **JS-rendered menus**: sites that render the menu client-side won't yield
  content to plain `requests`. Playwright is stubbed in as an opt-in
  dependency for exactly this case, kept out of the default path to protect
  runtime/cost.
- **False positives/negatives in menu validation**: keyword-based scoring is
  a heuristic, not NLP. Expect to tune `confidence_thresholds` and
  `menu_content_keywords` after reviewing the first few hundred results.
- **Free search fallback fragility**: DuckDuckGo's HTML endpoint has no
  official API contract and can change markup or rate-limit. It's used only
  as a fallback (OSM website tag is preferred), and failures degrade
  gracefully to `manual_review` rather than crashing the batch.
- **CI state persistence**: GitHub Actions cache is best-effort (LRU
  eviction, not guaranteed durable). The workflows commit CSV exports every
  run as the durable record; if you want the *database* itself durable
  across runs too, switch to committing `data/bar_radar.db` directly instead
  of relying on `actions/cache` (trade-off: bigger repo diffs).
- **Politeness / blocking**: `http_utils.py` adds a per-host delay and
  retry, but aggressive crawling of many small venue sites in a short window
  can still trip basic bot protection — hence `BLOCKED` as a first-class
  `menu_status` rather than a hard failure.
- **PDF menu quality**: scanned (image-only) PDFs won't yield extractable
  text via `pypdf` and will score low — these currently land in
  `manual_review` rather than being falsely marked `NO_MENU_FOUND`... note
  today they *do* fall to `NO_MENU_FOUND` if `pypdf` returns empty text;
  flagged here as a known Phase 1 simplification (OCR is a Phase 2 concern).

---

## Running locally (VS Code on Windows)

```powershell
# 1. Clone and set up a virtual environment
git clone https://github.com/YOUR_USERNAME/bar-radar.git
cd bar-radar
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Initialize the database (creates data/bar_radar.db from schema.sql)
python -c "from db.database import init_db; init_db()"

# 3. Run the test suite (no network required)
pytest -v

# 4. Discover candidates for a single city (small, fast smoke test)
python -m src.pipeline.run_discovery --cities Berlin --limit 1

# 5. Enrich a small batch and inspect results
python -m src.pipeline.run_enrichment --batch-size 10

# 6. Export CSVs
python -m src.pipeline.export
# -> data/exports/bar_radar_venues.csv
# -> data/exports/bar_radar_valid.csv
# -> data/manual_review/manual_review.csv
```

Open `data/bar_radar.db` with the "SQLite Viewer" VS Code extension (or
DB Browser for SQLite) to inspect rows directly while iterating.

**Before running against all ~1,000 target venues**, edit
`config/settings.yaml` → `http.user_agent` to include your real repo URL —
identifying your bot honestly is part of being a polite, free-tier-friendly
scraper.

---

## 7. Phase 2 — Menu Extraction Engine

Phase 2 takes every venue with a Phase 1-validated menu and (a) discovers
**every** relevant menu on that venue's site — not just the one Phase 1
happened to find — and (b) extracts the actual text/content of each one.

### 7.1 What changed in the data model

`menu_sources` is now genuinely one-to-many per venue (Phase 1 only ever
kept one "primary" row; Phase 2 adds as many rows as real menus exist:
cocktail, wine, beer, food, happy hour, brunch, etc). New columns, added
non-destructively via `db/migrations/001_phase2_menu_extraction.sql`:

```
menu_name              -- e.g. "Cocktail Menu", from link text or category
menu_category          -- COCKTAIL, DRINKS, WINE, BEER, SPIRITS, FOOD,
                        -- HAPPY_HOUR, BRUNCH, SEASONAL, ROOM_SERVICE, OTHER
discovery_method        -- how this URL was found (phase2_multi_menu_crawl, etc)
retrieval_method         -- requests_html, pdf_text, pdf_ocr, image_ocr,
                          -- playwright_render, screenshot_ocr
raw_file_path            -- evidence on disk: data/evidence/<venue_id>/<hash>.ext
extracted_text            -- the actual menu content
extraction_status          -- PENDING, EXTRACTED, PARTIAL, PDF_OCR,
                            -- SCREENSHOT_OCR, BLOCKED, FAILED, MANUAL_REVIEW
extraction_confidence        -- 0-1
content_hash                  -- sha256 of the raw fetched bytes
checked_at                     -- when this source was last attempted
```

A `UNIQUE(venue_id, menu_url)` index prevents the same menu ever being
inserted twice for a venue, satisfying "avoid duplicates when the same menu
is linked multiple times."

`extraction_retry_queue` holds venues where Phase 2 discovery found no menu
links at all (distinct from `manual_review`, which is Phase 1's website/menu
triage queue) - kept separate per the spec's "keep NONE/failed venues
separate for later retry."

### 7.2 Pipeline

```
src/extraction/
├── discovery.py             # finds ALL menu links (homepage + known menu page + common paths)
├── categorizer.py           # keyword classification into the fixed menu_category set
├── evidence_store.py        # saves raw HTML/PDF/image/screenshot to disk, computes content_hash
├── html_extractor.py        # requests + BeautifulSoup, flags pages needing JS rendering
├── pdf_extractor.py         # pdfplumber text-first, PyMuPDF-rendered-page OCR fallback
├── image_extractor.py       # direct OCR
├── playwright_fallback.py   # opt-in: render JS pages, screenshot+OCR as last resort
├── ocr_utils.py             # Tesseract wrapper (chosen over PaddleOCR - see below)
└── extractor.py             # dispatches a candidate to the right path above

src/pipeline/run_extraction.py   # CLI: discovery stage + extraction stage, both resumable
```

Run it:
```powershell
python -m src.pipeline.run_extraction --discovery-batch-size 50 --extraction-batch-size 150
python -m src.pipeline.export   # writes bar_radar_menu_sources.csv (all) and
                                  # bar_radar_menu_sources_extracted.csv (successful only)
```

### 7.3 Engineering rules this satisfies

- **Never overwrites one menu with another.** `insert_menu_source_if_new()`
  refuses to touch a row that already exists for `(venue_id, menu_url)`.
- **Resumable, not endlessly retried.** `extraction_status` is set exactly
  once per source under normal operation - `get_pending_extractions()` only
  picks up rows still at `PENDING`. A source that ends up `FAILED` or
  `BLOCKED` stays that way until you explicitly ask for a retry
  (`python -m src.pipeline.run_extraction --retry-failed`), rather than the
  same broken site being re-crawled every single batch forever.
- **The Phase 1 primary menu still gets extracted.** If Phase 2 discovery
  finds a URL that already exists as the Phase 1 "primary" row,
  `enroll_existing_menu_source_for_phase2()` attaches the Phase 2 fields to
  that existing row in place instead of skipping it - so the venue's main
  validated menu doesn't fall through the cracks just because it isn't
  "new."
- **Evidence saved, deduplicated by content.** `evidence_store.py` hashes
  content before writing, so identical content fetched twice (e.g. the same
  PDF linked from two pages) is stored once.
- **Batches, configurable size**, same pattern as Phase 1
  (`--discovery-batch-size` / `--extraction-batch-size`).

### 7.4 Why Tesseract over PaddleOCR

Both are free and local. Tesseract was chosen because it installs in one
`apt-get install tesseract-ocr tesseract-ocr-deu` line with no extra weight
on GitHub's Ubuntu runners, keeping CI fast; PaddleOCR is generally more
accurate on messy layouts but pulls in a much heavier ML stack. If OCR
quality on real scanned/photographed menus turns out to be a bottleneck,
`src/extraction/ocr_utils.py` is the single place to swap engines - nothing
else in the pipeline needs to change.

### 7.5 Local setup addition

```powershell
# Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
# and make sure it's on PATH, then:
pip install -r requirements.txt
python -m db.migrate           # adds Phase 2 columns to your existing DB, safely
python -m src.pipeline.run_extraction --discovery-batch-size 5 --extraction-batch-size 20
```

On GitHub Actions this is handled automatically by `.github/workflows/extract.yml`,
which installs Tesseract via `apt-get` before each run - no local setup
needed if you're running entirely through Actions.

### 7.6 Phase 3 readiness

Every extracted `menu_source` now has real text + a `content_hash`. Phase 3
(combining all of a venue's menus into one venue-level view, and detecting
changes over time) can build directly on this: `MENU_SNAPSHOT` (already
scaffolded in `db/schema.sql`) becomes "reprocess `menu_sources` on a
schedule, compare `content_hash` to the last snapshot, write a
`CHANGE_EVENT` on drift" - no schema changes needed to get started.

