import pytest

from db.database import get_conn
from db.migrate import run as run_migrations
from src.pipeline.import_manual_venues import _group_rows_by_venue, import_venue_group


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.database.db_path", lambda: tmp_path / "test.db")
    run_migrations()


def test_single_menu_venue_creates_one_venue_and_one_menu_source(db):
    rows = [{
        "venue_name": "Solo Bar", "city": "Berlin", "tier": "1",
        "website_url": "https://solobar.de",
        "menu_url": "https://solobar.de/menu.pdf",
        "menu_name": "Cocktail Menu", "menu_category": "COCKTAIL",
        "menu_source_type": "PDF", "is_primary": "true",
    }]
    groups = _group_rows_by_venue(rows)
    assert len(groups) == 1
    (name, city), venue_rows = groups[0]
    result = import_venue_group(name, city, venue_rows)
    assert result["status"] == "OK"

    with get_conn() as conn:
        venue = conn.execute("SELECT status FROM venues WHERE venue_name = ?", ("Solo Bar",)).fetchone()
        sources = conn.execute("SELECT * FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?)", ("Solo Bar",)).fetchall()
    assert venue["status"] == "ENRICHED"
    assert len(sources) == 1
    assert sources[0]["menu_category"] == "COCKTAIL"
    assert sources[0]["menu_source_type"] == "PDF"
    assert sources[0]["is_primary"] == 1
    assert sources[0]["discovery_method"] == "manual_curated"
    assert sources[0]["extraction_status"] == "PENDING"


def test_multi_menu_venue_groups_rows_into_one_venue(db):
    rows = [
        {"venue_name": "Multi Bar", "city": "Hamburg", "menu_url": "https://multibar.de/drinks", "menu_name": "Drinks", "is_primary": "true"},
        {"venue_name": "Multi Bar", "city": "Hamburg", "menu_url": "https://multibar.de/wine.pdf", "menu_name": "Wine List"},
        {"venue_name": "Multi Bar", "city": "Hamburg", "menu_url": "https://multibar.de/happy-hour", "menu_name": "Happy Hour"},
    ]
    groups = _group_rows_by_venue(rows)
    assert len(groups) == 1  # all three rows collapse into ONE venue
    (name, city), venue_rows = groups[0]
    assert len(venue_rows) == 3

    result = import_venue_group(name, city, venue_rows)
    assert result["status"] == "OK"
    assert "3 new menu source" in result["detail"]

    with get_conn() as conn:
        venue_count = conn.execute("SELECT COUNT(*) AS n FROM venues WHERE venue_name = ?", ("Multi Bar",)).fetchone()["n"]
        sources = conn.execute(
            "SELECT menu_url, is_primary FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?)",
            ("Multi Bar",),
        ).fetchall()
    assert venue_count == 1
    assert len(sources) == 3
    primary_rows = [s for s in sources if s["is_primary"] == 1]
    assert len(primary_rows) == 1
    assert primary_rows[0]["menu_url"] == "https://multibar.de/drinks"


def test_multi_image_menu_pages_all_attach_to_same_venue(db):
    rows = [
        {"venue_name": "Photo Menu Bar", "city": "Munich", "menu_url": f"https://photobar.de/img/menu{i}.jpg", "menu_name": f"Drinks Menu ({i}/4)", "is_primary": "true" if i == 1 else ""}
        for i in range(1, 5)
    ]
    groups = _group_rows_by_venue(rows)
    (name, city), venue_rows = groups[0]
    result = import_venue_group(name, city, venue_rows)
    assert result["status"] == "OK"

    with get_conn() as conn:
        sources = conn.execute(
            "SELECT menu_source_type, menu_name FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?)",
            ("Photo Menu Bar",),
        ).fetchall()
    assert len(sources) == 4
    assert all(s["menu_source_type"] == "IMAGE" for s in sources)


def test_primary_defaults_to_first_menu_row_when_unspecified(db):
    rows = [
        {"venue_name": "No Explicit Primary Bar", "city": "Berlin", "menu_url": "https://npbar.de/a"},
        {"venue_name": "No Explicit Primary Bar", "city": "Berlin", "menu_url": "https://npbar.de/b"},
    ]
    groups = _group_rows_by_venue(rows)
    (name, city), venue_rows = groups[0]
    import_venue_group(name, city, venue_rows)

    with get_conn() as conn:
        primary = conn.execute(
            "SELECT menu_url FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?) AND is_primary = 1",
            ("No Explicit Primary Bar",),
        ).fetchone()
    assert primary["menu_url"] == "https://npbar.de/a"


def test_menu_source_type_and_category_auto_detected_when_blank(db):
    rows = [{"venue_name": "Auto Detect Bar", "city": "Cologne", "menu_url": "https://autobar.de/cocktails.pdf"}]
    groups = _group_rows_by_venue(rows)
    (name, city), venue_rows = groups[0]
    import_venue_group(name, city, venue_rows)

    with get_conn() as conn:
        source = conn.execute(
            "SELECT menu_source_type, menu_category FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?)",
            ("Auto Detect Bar",),
        ).fetchone()
    assert source["menu_source_type"] == "PDF"
    assert source["menu_category"] == "COCKTAIL"


def test_rows_missing_name_or_city_are_dropped_during_grouping(db):
    rows = [
        {"venue_name": "", "city": "Berlin", "menu_url": "https://x.de/a"},
        {"venue_name": "Valid Bar", "city": "", "menu_url": "https://x.de/b"},
        {"venue_name": "Valid Bar", "city": "Berlin", "menu_url": "https://x.de/c"},
    ]
    groups = _group_rows_by_venue(rows)
    assert len(groups) == 1
    (name, city), venue_rows = groups[0]
    assert name == "Valid Bar" and city == "Berlin"
    assert len(venue_rows) == 1


def test_rerunning_import_does_not_duplicate_menu_sources(db):
    rows = [{"venue_name": "Idempotent Bar", "city": "Berlin", "menu_url": "https://idem.de/menu"}]
    groups = _group_rows_by_venue(rows)
    (name, city), venue_rows = groups[0]

    first = import_venue_group(name, city, venue_rows)
    second = import_venue_group(name, city, venue_rows)

    assert first["status"] == "OK"
    assert "0 new menu source" in second["detail"]
    assert "already existed" in second["detail"]

    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM menu_sources WHERE venue_id = (SELECT venue_id FROM venues WHERE venue_name = ?)",
            ("Idempotent Bar",),
        ).fetchone()["n"]
    assert count == 1  # re-running the same import never creates a duplicate row


def test_venue_only_row_with_no_menu_url_stays_new(db):
    rows = [{"venue_name": "Website Only Bar", "city": "Berlin", "website_url": "https://webonly.de"}]
    groups = _group_rows_by_venue(rows)
    (name, city), venue_rows = groups[0]
    result = import_venue_group(name, city, venue_rows)
    assert result["status"] == "OK"

    with get_conn() as conn:
        venue = conn.execute("SELECT status, website_url FROM venues WHERE venue_name = ?", ("Website Only Bar",)).fetchone()
    assert venue["status"] == "NEW"
    assert venue["website_url"] == "https://webonly.de"


def test_flags_possible_duplicate_of_existing_venue(db):
    rows1 = [{"venue_name": "Buck and Breck", "city": "Berlin", "menu_url": "https://buckandbreck.com/menu"}]
    groups1 = _group_rows_by_venue(rows1)
    import_venue_group(*groups1[0][0], groups1[0][1])

    rows2 = [{"venue_name": "Buck and Breck Bar", "city": "Berlin", "menu_url": "https://buckandbreck.com/other"}]
    groups2 = _group_rows_by_venue(rows2)
    result = import_venue_group(*groups2[0][0], groups2[0][1])

    assert result["status"] == "OK"
    assert "WARNING" in result["detail"]
