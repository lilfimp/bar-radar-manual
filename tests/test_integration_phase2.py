"""End-to-end test of the Phase 2 pipeline (discovery stage + extraction
stage) against a fake multi-menu venue, with all HTTP calls mocked so this
runs with zero network access. Exercises the real DB wiring, not just
individual functions in isolation.
"""
from __future__ import annotations

import uuid

import pytest

from db.database import get_conn, upsert_venue, upsert_menu_source
from db.migrate import run as run_migrations
from src.pipeline.run_extraction import run_discovery_stage, run_extraction_stage

HOMEPAGE_HTML = """
<html><body>
<nav><a href="/cocktails">Cocktails</a></nav>
<a href="/wine">Wine List</a>
<a href="/about">About</a>
</body></html>
"""

COCKTAIL_PAGE_HTML = """
<html><body>
<h1>Cocktail Menu</h1>
<p>Try our gin, vodka, rum and whisky based signature drinks. Longdrinks also available.</p>
</body></html>
"""

WINE_PAGE_HTML = """
<html><body>
<h1>Wine List</h1>
<p>A curated wein selection featuring bier and sekt as well as a range of prosecco
by the glass. Our sommelier has picked wines to pair with every dish on the menu,
from light spritzers to full-bodied reds, refreshed each season.</p>
</body></html>
"""


class FakeResponse:
    def __init__(self, text="", status_code=200, content_type="text/html"):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = {"content-type": content_type}


@pytest.fixture
def fake_venue(tmp_path, monkeypatch):
    # Isolate just the DB file and evidence storage into a tmp directory -
    # leave config loading (settings.yaml, cities.yaml) pointed at the real
    # repo, since those files aren't meant to be duplicated per-test.
    monkeypatch.setattr("db.database.db_path", lambda: tmp_path / "test.db")

    import src.extraction.evidence_store as store
    monkeypatch.setattr(store, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(store, "EVIDENCE_ROOT", tmp_path / "evidence")

    run_migrations()

    venue_id = str(uuid.uuid4())
    upsert_venue({
        "venue_id": venue_id,
        "venue_name": "Test Bar",
        "city": "Berlin",
        "tier": 1,
        "category": "cocktail_bar",
        "address": "Teststr. 1",
        "latitude": 52.5,
        "longitude": 13.4,
        "osm_type": "node",
        "osm_id": 1,
        "website_url": "https://testbar.de/",
        "website_status": "FOUND",
        "discovery_source": "overpass_osm",
        "discovery_query": "test",
        "venue_confidence": 0.9,
        "status": "ENRICHED",
    })
    upsert_menu_source({
        "menu_source_id": str(uuid.uuid4()),
        "venue_id": venue_id,
        "menu_url": "https://testbar.de/cocktails",
        "menu_source_type": "HTML_PAGE",
        "menu_status": "VALID_MENU",
        "menu_confidence": 0.9,
    })
    return venue_id


def _fake_get(url, **kwargs):
    if url.rstrip("/") == "https://testbar.de":
        return FakeResponse(HOMEPAGE_HTML)
    if "cocktails" in url:
        return FakeResponse(COCKTAIL_PAGE_HTML)
    if "wine" in url:
        return FakeResponse(WINE_PAGE_HTML)
    return FakeResponse(status_code=404)


def test_discovers_and_extracts_multiple_menus(fake_venue, monkeypatch):
    monkeypatch.setattr("src.extraction.discovery.get", _fake_get)
    monkeypatch.setattr("src.extraction.html_extractor.get", _fake_get)

    run_discovery_stage(50)
    # "wine" is newly found; "cocktails" already existed as the Phase 1
    # primary row and gets enrolled in-place rather than re-inserted - see
    # test_existing_primary_row_gets_enrolled_not_duplicated below.
    run_extraction_stage(150)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT menu_url, menu_category, extraction_status, is_primary FROM menu_sources WHERE venue_id = ?",
            (fake_venue,),
        ).fetchall()

    urls = {r["menu_url"] for r in rows}
    assert any("cocktails" in u for u in urls)
    assert any("wine" in u for u in urls)
    assert len(rows) == 2  # no duplicate row for the pre-existing cocktails URL

    categories = {r["menu_category"] for r in rows}
    assert "COCKTAIL" in categories
    assert "WINE" in categories

    primary_rows = [r for r in rows if r["is_primary"] == 1]
    assert len(primary_rows) == 1
    assert "cocktails" in primary_rows[0]["menu_url"]

    cocktail_row = next(r for r in rows if "cocktails" in r["menu_url"])
    assert cocktail_row["extraction_status"] in ("EXTRACTED", "PARTIAL")  # was actually processed, not skipped forever


def test_existing_primary_row_gets_enrolled_not_duplicated(fake_venue, monkeypatch):
    monkeypatch.setattr("src.extraction.discovery.get", _fake_get)
    monkeypatch.setattr("src.extraction.html_extractor.get", _fake_get)

    run_discovery_stage(50)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT discovery_method, extraction_status FROM menu_sources WHERE venue_id = ? AND menu_url = ?",
            (fake_venue, "https://testbar.de/cocktails"),
        ).fetchone()
    assert row["discovery_method"] == "phase2_multi_menu_crawl"
    assert row["extraction_status"] == "PENDING"


def test_second_discovery_run_does_not_duplicate(fake_venue, monkeypatch):
    monkeypatch.setattr("src.extraction.discovery.get", _fake_get)
    monkeypatch.setattr("src.extraction.html_extractor.get", _fake_get)

    run_discovery_stage(50)
    with get_conn() as conn:
        count_after_first = conn.execute(
            "SELECT COUNT(*) AS n FROM menu_sources WHERE venue_id = ?", (fake_venue,)
        ).fetchone()["n"]

    run_discovery_stage(50)  # should skip - venue already has discovery_method set
    with get_conn() as conn:
        count_after_second = conn.execute(
            "SELECT COUNT(*) AS n FROM menu_sources WHERE venue_id = ?", (fake_venue,)
        ).fetchone()["n"]

    assert count_after_first == count_after_second


def test_extraction_is_resumable_and_skips_already_attempted(fake_venue, monkeypatch):
    monkeypatch.setattr("src.extraction.discovery.get", _fake_get)
    monkeypatch.setattr("src.extraction.html_extractor.get", _fake_get)

    run_discovery_stage(50)
    first_pass = run_extraction_stage(150)
    assert first_pass > 0

    second_pass = run_extraction_stage(150)
    assert second_pass == 0  # every source already has an outcome - none left PENDING


def test_failed_source_requires_explicit_retry(fake_venue, monkeypatch):
    from db.database import requeue_for_retry, update_menu_source_extraction, get_all_menu_sources_for_venue

    monkeypatch.setattr("src.extraction.discovery.get", _fake_get)
    monkeypatch.setattr("src.extraction.html_extractor.get", _fake_get)

    run_discovery_stage(50)
    run_extraction_stage(150)

    # Simulate one source having ended up FAILED (e.g. a transient network error).
    source = get_all_menu_sources_for_venue(fake_venue)[0]
    update_menu_source_extraction(source["menu_source_id"], {
        "extraction_status": "FAILED", "extraction_confidence": 0.0,
        "retrieval_method": None, "raw_file_path": None, "content_hash": None,
        "extracted_text": None, "menu_category": source["menu_category"], "menu_name": source["menu_name"],
    })

    # Without --retry-failed, it should NOT be picked up again automatically.
    assert run_extraction_stage(150) == 0

    # With an explicit retry request, it should be re-queued and re-attempted.
    requeued = requeue_for_retry()
    assert requeued == 1
    assert run_extraction_stage(150) == 1
