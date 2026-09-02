"""Playwright-based rendering for JS-heavy menu pages.

This is intentionally the LAST resort in the extraction chain (see
extractor.py) - it's the slowest and heaviest dependency in the stack,
and most sites never need it. It's an optional dependency (see
requirements.txt) so the baseline pipeline works without it; if Playwright
isn't installed, this module degrades to returning "not available" rather
than crashing the batch.

Two things happen here in sequence:
1. Render the page with a real browser and re-extract visible text - this
   alone fixes the majority of "JS shell" cases.
2. If text is STILL too short after rendering, take a full-page screenshot
   and OCR it as the final fallback.
"""
from __future__ import annotations

from src.extraction.evidence_store import content_hash, save_evidence
from src.extraction.ocr_utils import ocr_image_bytes
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

MIN_USABLE_TEXT_CHARS = 120

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def render_and_extract(url: str, venue_id: str) -> dict:
    if not PLAYWRIGHT_AVAILABLE:
        log.info("Playwright not installed - cannot render %s", url)
        return _unavailable()

    try:
        from bs4 import BeautifulSoup

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, timeout=30_000, wait_until="networkidle")
            html = page.content()

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(("script", "style", "noscript", "svg", "nav", "footer")):
                tag.decompose()
            text = soup.get_text("\n", strip=True)

            if len(text) >= MIN_USABLE_TEXT_CHARS:
                browser.close()
                raw_bytes = html.encode("utf-8")
                return {
                    "extracted_text": text,
                    "extraction_status": "EXTRACTED",
                    "extraction_confidence": 0.75,
                    "retrieval_method": "playwright_render",
                    "raw_file_path": save_evidence(venue_id, raw_bytes, kind="html"),
                    "content_hash": content_hash(raw_bytes),
                }

            # Still too thin - screenshot and OCR as the final fallback.
            screenshot_bytes = page.screenshot(full_page=True)
            browser.close()
            ocr_text, ocr_confidence = ocr_image_bytes(screenshot_bytes)
            status = "SCREENSHOT_OCR" if ocr_text.strip() else "MANUAL_REVIEW"
            return {
                "extracted_text": ocr_text,
                "extraction_status": status,
                "extraction_confidence": round(ocr_confidence, 2),
                "retrieval_method": "screenshot_ocr",
                "raw_file_path": save_evidence(venue_id, screenshot_bytes, kind="screenshot"),
                "content_hash": content_hash(screenshot_bytes),
            }
    except Exception as exc:  # noqa: BLE001 - real-world sites fail renders in many ways
        log.warning("Playwright render failed for %s: %s", url, exc)
        return _unavailable()


def _unavailable() -> dict:
    return {
        "extracted_text": "",
        "extraction_status": "FAILED",
        "extraction_confidence": 0.0,
        "retrieval_method": "playwright_render",
        "raw_file_path": None,
        "content_hash": None,
    }
