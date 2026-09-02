"""Lightweight migration runner for BAR RADAR.

Why not just edit schema.sql? Your production database already has real
discovered/enriched data in it (venues, menu_sources). `CREATE TABLE IF NOT
EXISTS` in schema.sql only helps *new* databases - it does nothing to add
columns to a table that already exists. This runner applies incremental,
additive-only migrations (ALTER TABLE ADD COLUMN, CREATE INDEX, CREATE
TABLE) against an existing database, and tracks what's already been applied
so re-running is always safe.

Usage:
    python -m db.migrate
"""
from __future__ import annotations

from pathlib import Path

from db.database import get_conn, init_db
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def _ensure_migrations_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            filename    TEXT NOT NULL,
            applied_at  TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _applied_versions(conn) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def _available_migrations() -> list[tuple[int, Path]]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    out = []
    for f in files:
        # filenames are "NNN_description.sql"
        version = int(f.stem.split("_")[0])
        out.append((version, f))
    return out


def run() -> None:
    # Make sure a brand-new DB has at least the base schema before migrating.
    init_db()

    with get_conn() as conn:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)

        for version, path in _available_migrations():
            if version in applied:
                continue
            log.info("Applying migration %s (%s)", version, path.name)
            with open(path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            conn.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES (?, ?)",
                (version, path.name),
            )
            conn.commit()
            log.info("Migration %s applied.", version)

    log.info("All migrations up to date.")


if __name__ == "__main__":
    run()
