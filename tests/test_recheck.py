import uuid

import pytest

from db.database import (
    add_manual_review,
    get_conn,
    requeue_venues_for_recheck,
    upsert_menu_source,
    upsert_venue,
)
from db.migrate import run as run_migrations


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.database.db_path", lambda: tmp_path / "test.db")
    run_migrations()


def _make_venue(venue_id, city="Berlin", status="ENRICHED"):
    upsert_venue({
        "venue_id": venue_id,
        "venue_name": f"Bar {venue_id[:6]}",
        "city": city,
        "tier": 1,
        "category": "bar",
        "address": None,
        "latitude": 52.5,
        "longitude": 13.4,
        "osm_type": "node",
        "osm_id": 1,
        "website_url": "https://example.de",
        "website_status": "FOUND",
        "discovery_source": "overpass_osm",
        "discovery_query": "test",
        "venue_confidence": 0.9,
        "status": status,
    })


def _make_primary_menu_source(venue_id, menu_status):
    upsert_menu_source({
        "menu_source_id": str(uuid.uuid4()),
        "venue_id": venue_id,
        "menu_url": "https://example.de/menu",
        "menu_source_type": "HTML_PAGE",
        "menu_status": menu_status,
        "menu_confidence": 0.2,
    })


def test_requeue_picks_up_no_menu_found_and_possible_menu(db):
    v1, v2, v3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    _make_venue(v1)
    _make_venue(v2)
    _make_venue(v3)
    _make_primary_menu_source(v1, "NO_MENU_FOUND")
    _make_primary_menu_source(v2, "POSSIBLE_MENU")
    _make_primary_menu_source(v3, "VALID_MENU")  # should NOT be requeued

    n = requeue_venues_for_recheck()
    assert n == 2

    with get_conn() as conn:
        statuses = {
            r["venue_id"]: r["status"]
            for r in conn.execute("SELECT venue_id, status FROM venues").fetchall()
        }
    assert statuses[v1] == "NEW"
    assert statuses[v2] == "NEW"
    assert statuses[v3] == "ENRICHED"  # untouched


def test_requeue_respects_city_filter(db):
    berlin_id, hamburg_id = str(uuid.uuid4()), str(uuid.uuid4())
    _make_venue(berlin_id, city="Berlin")
    _make_venue(hamburg_id, city="Hamburg")
    _make_primary_menu_source(berlin_id, "NO_MENU_FOUND")
    _make_primary_menu_source(hamburg_id, "NO_MENU_FOUND")

    n = requeue_venues_for_recheck(city="Berlin")
    assert n == 1

    with get_conn() as conn:
        statuses = {
            r["venue_id"]: r["status"]
            for r in conn.execute("SELECT venue_id, status FROM venues").fetchall()
        }
    assert statuses[berlin_id] == "NEW"
    assert statuses[hamburg_id] == "ENRICHED"


def test_requeue_resolves_old_manual_review_entries(db):
    v1 = str(uuid.uuid4())
    _make_venue(v1)
    _make_primary_menu_source(v1, "NO_MENU_FOUND")
    add_manual_review(str(uuid.uuid4()), v1, "MENU", "No menu-like links found on site")

    requeue_venues_for_recheck()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT resolved FROM manual_review WHERE venue_id = ?", (v1,)
        ).fetchone()
    assert row["resolved"] == 1


def test_requeue_is_a_noop_when_nothing_matches(db):
    v1 = str(uuid.uuid4())
    _make_venue(v1)
    _make_primary_menu_source(v1, "VALID_MENU")

    assert requeue_venues_for_recheck() == 0
