"""Validate that a candidate menu URL actually contains drinks-menu content,
and produce a confidence score + menu_status.

Text extraction:
- HTML_PAGE: strip tags, lowercase, keyword-match.
- PDF: extract text with pypdf (pure Python, no system deps).
- IMAGE: we cannot OCR for free reliably at this volume -> treat as
  POSSIBLE_MENU capped at a modest confidence, flagged for manual review.
- EXTERNAL_PLATFORM: reachable => POSSIBLE_MENU (content lives off-site,
  can't easily verify without a browser).

Confidence combines two independent signals:
1. Keyword hits (drink category words AND specific drink names - a menu
   that lists "Negroni 12€" instead of the word "cocktail" should still
   score well).
2. Price-pattern density (repeated "12,50 €" / "€ 9.50" style patterns) -
   one of the strongest real-world signals that a page is a priced menu,
   independent of which specific words it uses.
"""
from __future__ import annotations

import io
import re

from bs4 import BeautifulSoup

from src.utils.config import settings
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

# Matches "12,50 €", "9.00€", "€ 8", "9€", "12,-", etc. - common German/
# European menu price formats, with or without a decimal/cent portion.
PRICE_PATTERN = re.compile(r"(?:\d+(?:[.,]\d{1,2})?\s?€|€\s?\d+(?:[.,]\d{1,2})?|\d+,-\s?€?)")


def _keyword_hit_ratio(text: str) -> float:
    keywords = settings()["menu_content_keywords"]
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return min(hits / 5.0, 1.0)  # 5+ distinct keyword hits => full keyword confidence


def _price_signal(text: str) -> float:
    matches = PRICE_PATTERN.findall(text)
    return min(len(matches) / 6.0, 1.0)  # 6+ price-like patterns => full price confidence


def _combined_confidence(text: str) -> float:
    keyword_score = _keyword_hit_ratio(text)
    price_score = _price_signal(text)
    # Keyword hits are the primary signal; a strong price pattern gives a
    # meaningful boost on its own (a page with 6+ prices and zero drink
    # keywords is still very likely a menu of some kind) but doesn't alone
    # guarantee VALID_MENU, since e.g. a food-only menu also has prices.
    return min(keyword_score + 0.4 * price_score, 1.0)


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf not installed - cannot extract PDF text")
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:6])
    except Exception as exc:  # noqa: BLE001 - malformed PDFs are common in the wild
        log.warning("Failed to parse PDF: %s", exc)
        return ""


def validate_candidate(candidate: dict) -> dict:
    """Returns {"menu_status", "menu_confidence", "menu_url", "menu_source_type"}."""
    url = candidate["url"]
    source_type = candidate["source_type"]
    thresholds = settings()["confidence_thresholds"]

    resp = get(url)
    if resp is None:
        return _result(url, source_type, "WEBSITE_UNAVAILABLE", 0.0)
    if resp.status_code in (403, 429):
        return _result(url, source_type, "BLOCKED", 0.0)
    if resp.status_code >= 400:
        return _result(url, source_type, "WEBSITE_UNAVAILABLE", 0.0)

    if source_type == "PDF":
        text = _extract_pdf_text(resp.content)
        confidence = _combined_confidence(text) if text else 0.15
    elif source_type == "HTML_PAGE":
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        confidence = _combined_confidence(text)
    elif source_type == "IMAGE":
        # reachable image with menu-ish link text but no OCR -> capped confidence
        confidence = 0.4
    elif source_type == "EXTERNAL_PLATFORM":
        confidence = 0.5
    else:
        confidence = 0.0

    if confidence >= thresholds["valid_menu"]:
        status = "VALID_MENU"
    elif confidence >= thresholds["possible_menu"]:
        status = "POSSIBLE_MENU"
    else:
        status = "NO_MENU_FOUND"

    return _result(url, source_type, status, round(confidence, 2))


def _result(url: str, source_type: str, status: str, confidence: float) -> dict:
    return {
        "menu_url": url,
        "menu_source_type": source_type,
        "menu_status": status,
        "menu_confidence": confidence,
    }


def pick_best_valid_menu(candidates: list[dict]) -> dict | None:
    """Validate candidates in score order, return the first VALID_MENU or
    POSSIBLE_MENU result. Stops early on first VALID_MENU to save requests."""
    best_possible = None
    for candidate in candidates:
        result = validate_candidate(candidate)
        if result["menu_status"] == "VALID_MENU":
            return result
        if result["menu_status"] == "POSSIBLE_MENU" and best_possible is None:
            best_possible = result
    return best_possible
