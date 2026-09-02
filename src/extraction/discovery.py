"""Discover ALL relevant menu links for a venue, not just one.

Starting points:
1. The venue's homepage (website_url)
2. The already-known Phase 1 menu_url (often a "menu hub" page that links
   out to separate cocktail/wine/food/happy-hour pages, or is itself the
   only menu)

We collect every link on both pages whose text/href suggests it's a menu
(reusing the same keyword approach as Phase 1's crawler, generalized to
return ALL matches instead of stopping at the best one), plus a probe of
common menu-ish URL paths. Everything is deduplicated by normalized URL
before being returned, so the same menu linked from two places only shows
up once.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.utils.config import settings
from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

PDF_EXT = ".pdf"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# Extra menu-type keywords beyond Phase 1's generic set, so multi-menu
# venues (wine list, happy hour, brunch, room service...) are actually found
# instead of just the first "menu"-labelled link.
EXTRA_LINK_KEYWORDS = [
    "wine", "wein", "beer", "bier", "spirits", "spirituosen",
    "happy hour", "brunch", "room service", "seasonal", "saison",
    "food", "speisekarte", "cocktail",
]


def normalize_url(url: str) -> str:
    """Strip fragments/trailing slashes so the same page linked two
    different ways still dedupes to one entry."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _classify_source_type(url: str) -> str:
    lower = url.lower()
    if lower.endswith(PDF_EXT):
        return "PDF"
    if lower.endswith(IMAGE_EXTS):
        return "IMAGE"
    external_platforms = ("menulog", "toasttab", "gloriafood", "resmio", "noiceapp", "orderbird")
    if any(p in urlparse(url).netloc for p in external_platforms):
        return "EXTERNAL_PLATFORM"
    return "HTML_PAGE"


def _all_menu_keywords() -> list[str]:
    base = settings()["menu_link_keywords"]
    return list(dict.fromkeys(base + EXTRA_LINK_KEYWORDS))  # dedupe, keep order


def _score_link(text: str, href: str) -> int:
    keywords = _all_menu_keywords()
    haystack = f"{text} {href}".lower()
    return sum(1 for kw in keywords if kw in haystack)


def _extract_links_from_page(page_url: str, html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full_url = urljoin(page_url, href)
        if urlparse(full_url).netloc != urlparse(page_url).netloc:
            continue  # stay on-site; external menu platforms are still
            # caught via _classify_source_type when linked directly, but we
            # don't follow arbitrary third-party links
        text = a.get_text(" ", strip=True)
        score = _score_link(text, href)
        if score == 0:
            continue
        key = normalize_url(full_url)
        source_type = _classify_source_type(full_url)
        if source_type in ("PDF", "IMAGE"):
            score += 2
        existing = found.get(key)
        if not existing or score > existing["score"]:
            found[key] = {
                "url": full_url,
                "source_type": source_type,
                "link_text": text,
                "score": score,
            }
    return found


def _probe_common_paths(base_url: str) -> dict[str, dict]:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    found = {}
    for path in settings()["common_menu_paths"]:
        url = root + path
        resp = get(url)
        if resp is not None and resp.status_code == 200:
            key = normalize_url(url)
            found[key] = {
                "url": url,
                "source_type": _classify_source_type(url),
                "link_text": path.strip("/"),
                "score": 3,
            }
    return found


def discover_all_menu_links(website_url: str | None, known_menu_url: str | None) -> list[dict]:
    """Returns a deduplicated list of {"url", "source_type", "link_text",
    "score"} candidates gathered from the homepage and the known menu page.
    Always includes known_menu_url itself (as the guaranteed-primary entry)
    even if the crawl logic wouldn't otherwise have scored it.
    """
    all_candidates: dict[str, dict] = {}

    pages_to_scan = [u for u in (website_url, known_menu_url) if u]
    for page_url in pages_to_scan:
        resp = get(page_url)
        if resp is None or resp.status_code >= 400:
            log.info("Could not fetch %s for link discovery", page_url)
            continue
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and not resp.text.strip().startswith("<"):
            continue  # it's a PDF/image itself, not a page to scan for links
        links = _extract_links_from_page(page_url, resp.text)
        for key, candidate in links.items():
            existing = all_candidates.get(key)
            if not existing or candidate["score"] > existing["score"]:
                all_candidates[key] = candidate

    # Homepage/menu-page links were thin - also probe conventional paths.
    if website_url and len(all_candidates) < 3:
        for key, candidate in _probe_common_paths(website_url).items():
            all_candidates.setdefault(key, candidate)

    # Guarantee the already-known Phase 1 menu URL is always present.
    if known_menu_url:
        key = normalize_url(known_menu_url)
        all_candidates.setdefault(
            key,
            {
                "url": known_menu_url,
                "source_type": _classify_source_type(known_menu_url),
                "link_text": "",
                "score": 5,  # already validated in Phase 1 - trust it
            },
        )

    return sorted(all_candidates.values(), key=lambda c: c["score"], reverse=True)
