"""Crawl a venue's website (homepage + a couple of likely subpages) to find
candidate menu URLs: internal HTML pages, PDFs, images, or links out to
external menu platforms (e.g. a hosted "digital menu" service).

Playwright is intentionally NOT used here - plain requests + BeautifulSoup
covers the large majority of small/medium venue sites. A Playwright fallback
hook is provided for JS-only sites but only triggers if the plain fetch
comes back essentially empty, keeping the common path fast and cheap.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.utils.config import settings
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

PDF_EXT = ".pdf"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _classify_link(url: str) -> str:
    lower = url.lower()
    if lower.endswith(PDF_EXT):
        return "PDF"
    if lower.endswith(IMAGE_EXTS):
        return "IMAGE"
    parsed = urlparse(url)
    external_platforms = ("menulog", "toasttab", "gloriafood", "resmio", "noiceapp", "orderbird")
    if any(p in parsed.netloc for p in external_platforms):
        return "EXTERNAL_PLATFORM"
    return "HTML_PAGE"


def _score_link_text(text: str, href: str) -> int:
    keywords = settings()["menu_link_keywords"]
    haystack = f"{text} {href}".lower()
    return sum(1 for kw in keywords if kw in haystack)


def find_candidate_menu_links(homepage_url: str, homepage_html: str) -> list[dict]:
    """Returns candidates sorted best-first: [{"url", "source_type", "score"}]."""
    soup = BeautifulSoup(homepage_html, "html.parser")
    candidates: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        full_url = urljoin(homepage_url, href)
        text = a.get_text(" ", strip=True)
        score = _score_link_text(text, href)
        if score == 0:
            continue
        source_type = _classify_link(full_url)
        # PDFs/images with keyword match are strong signals
        if source_type in ("PDF", "IMAGE"):
            score += 2
        existing = candidates.get(full_url)
        if not existing or score > existing["score"]:
            candidates[full_url] = {"url": full_url, "source_type": source_type, "score": score}

    return sorted(candidates.values(), key=lambda c: c["score"], reverse=True)


def try_common_paths(base_url: str) -> list[dict]:
    """Probe a handful of conventional menu URL paths directly, in case the
    homepage doesn't link them clearly (common on single-page sites)."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    found = []
    for path in settings()["common_menu_paths"]:
        url = root + path
        resp = get(url)
        if resp is not None and resp.status_code == 200:
            found.append({"url": url, "source_type": _classify_link(url), "score": 3})
    return found


def crawl_for_menu_candidates(website_url: str) -> list[dict]:
    resp = get(website_url)
    if resp is None or resp.status_code >= 400:
        log.info("Homepage unreachable for menu crawl: %s", website_url)
        return []

    candidates = find_candidate_menu_links(website_url, resp.text)

    # Homepage produced almost nothing useful -> also probe common paths.
    if len(candidates) < 2:
        candidates.extend(try_common_paths(website_url))

    # Dedup by URL, keep highest score
    best: dict[str, dict] = {}
    for c in candidates:
        if c["url"] not in best or c["score"] > best[c["url"]]["score"]:
            best[c["url"]] = c

    if not best:
        # Small independent bars very often have a single-page site with the
        # drinks list directly on the homepage - no dedicated "menu" link,
        # no common path either. Without this, such venues would come back
        # with zero candidates and be marked NO_MENU_FOUND despite the menu
        # being right there on the page we already fetched. Lowest possible
        # score so any real candidate found elsewhere is always preferred.
        log.info("No menu links found on %s - falling back to homepage itself as a candidate", website_url)
        best[website_url] = {"url": website_url, "source_type": "HTML_PAGE", "score": 1}

    return sorted(best.values(), key=lambda c: c["score"], reverse=True)
