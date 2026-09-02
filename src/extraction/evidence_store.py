"""Persist raw fetched menu content as evidence, and compute content hashes.

Every menu we fetch - HTML, PDF, image, or a Playwright screenshot - gets
saved to disk under data/evidence/<venue_id>/<hash>.<ext> before any text
extraction happens. This makes extraction auditable (you can always go look
at exactly what was fetched) and re-runnable offline without hitting the
network again.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from src.utils.config import REPO_ROOT

EVIDENCE_ROOT = REPO_ROOT / "data" / "evidence"

EXT_BY_KIND = {
    "html": ".html",
    "pdf": ".pdf",
    "image": ".img",  # actual extension appended by caller when known
    "screenshot": ".png",
}


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_evidence(venue_id: str, content: bytes, kind: str, ext: str | None = None) -> str:
    """Saves content bytes and returns the relative path (from repo root) as
    a string, suitable for storing in menu_sources.raw_file_path."""
    venue_dir = EVIDENCE_ROOT / venue_id
    venue_dir.mkdir(parents=True, exist_ok=True)

    file_hash = content_hash(content)
    extension = ext or EXT_BY_KIND.get(kind, ".bin")
    filename = f"{file_hash[:16]}{extension}"
    path = venue_dir / filename

    if not path.exists():  # identical content already saved - don't rewrite
        path.write_bytes(content)

    return str(path.relative_to(REPO_ROOT))


def read_evidence(relative_path: str) -> bytes:
    return (REPO_ROOT / relative_path).read_bytes()
