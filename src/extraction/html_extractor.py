"""Extract visible text from an HTML menu page.

Plain requests + BeautifulSoup handles the large majority of sites. If the
extracted text is suspiciously short (a strong signal the page is a JS
shell with content injected client-side), we escalate to Playwright to
render the page for real. If even that fails to produce usable text, the
page is screenshotted and OCR'd as a last resort - see ocr_utils.py.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from src.extraction.evidence_store import content_hash, save_evidence
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

# Below this many characters of visible text, assume the page needs JS
# rendering rather than treating it as "just a short menu".
MIN_USABLE_TEXT_CHARS = 120

# Tags whose contents are never real menu content.
NOISE_TAGS = ("script", "style", "noscript", "svg", "nav", "footer")


def _extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def extract_html(url: str, venue_id: str) -> dict:
    """Returns a dict with extracted_text, extraction_status, retrieval_method,
    raw_file_path, content_hash, needs_js (bool, for the caller to decide on
    a Playwright escalation)."""
    resp = get(url)
    if resp is None:
        return _fail(status="WEBSITE_UNAVAILABLE" if False else "FAILED", reason="no response")
    if resp.status_code in (403, 429):
        return _fail(status="BLOCKED", reason=f"http {resp.status_code}")
    if resp.status_code >= 400:
        return _fail(status="FAILED", reason=f"http {resp.status_code}")

    raw_bytes = resp.content
    raw_path = save_evidence(venue_id, raw_bytes, kind="html")
    text = _extract_visible_text(resp.text)

    needs_js = len(text) < MIN_USABLE_TEXT_CHARS
    return {
        "extracted_text": text,
        "extraction_status": "PARTIAL" if needs_js else "EXTRACTED",
        "retrieval_method": "requests_html",
        "raw_file_path": raw_path,
        "content_hash": content_hash(raw_bytes),
        "needs_js": needs_js,
    }


def _fail(status: str, reason: str) -> dict:
    log.info("HTML extraction failed: %s", reason)
    return {
        "extracted_text": "",
        "extraction_status": status,
        "retrieval_method": "requests_html",
        "raw_file_path": None,
        "content_hash": None,
        "needs_js": False,
    }
