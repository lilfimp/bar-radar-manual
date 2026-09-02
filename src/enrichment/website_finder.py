"""Find the official website for a venue.

Order of operations (cheapest/most reliable first):
1. Already have it from OSM tags (website_status == 'FOUND' at discovery time).
2. Free fallback: try a chain of free web search engines' plain HTML result
   pages, in order (currently DuckDuckGo, then Bing). Each has its own
   circuit breaker - one engine getting IP-blocked doesn't take the other
   down with it, and a query only fails outright if EVERY engine in the
   chain fails for it.

Important honesty note: none of these have an official scraping API
contract, and shared/datacenter IP ranges (like GitHub Actions runners) get
treated with more suspicion than a residential IP. Having two engines
meaningfully improves resilience over having one, but if both end up
IP-blocked from the same runner pool, no amount of client-side tuning fixes
that - see README section 6 for the honest limitation and fallback options.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from src.utils.http_utils import get
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

# domains that are never the venue's own website
BLOCKED_RESULT_DOMAINS = (
    "facebook.com", "instagram.com", "tripadvisor.", "yelp.",
    "google.com", "maps.google", "wikipedia.org", "opentable.",
    "thefork.", "lieferando.", "ubereats.", "bing.com", "duckduckgo.com",
)


class CircuitBreaker:
    """Stops calling a fragile endpoint after too many consecutive failures,
    instead of burning the batch's time budget on requests that were never
    going to succeed. Resets naturally every run (fresh process)."""

    def __init__(self, name: str, threshold: int = 5):
        self.name = name
        self.threshold = threshold
        self.consecutive_failures = 0
        self.open = False

    def record(self, success: bool) -> None:
        if success:
            self.consecutive_failures = 0
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold and not self.open:
            self.open = True
            log.error(
                "%s circuit breaker OPEN after %d consecutive failures - "
                "skipping %s for the rest of this run.",
                self.name, self.consecutive_failures, self.name,
            )

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.open = False


_ddg_breaker = CircuitBreaker("DuckDuckGo")
_bing_breaker = CircuitBreaker("Bing")


def reset_ddg_circuit_breaker() -> None:
    """Exposed for tests and for a future --retry-failed-style rerun."""
    _ddg_breaker.reset()


def reset_bing_circuit_breaker() -> None:
    _bing_breaker.reset()


def _valid_result(url: str | None) -> str | None:
    if not url:
        return None
    if any(bad in url for bad in BLOCKED_RESULT_DOMAINS):
        return None
    return url


def _clean_ddg_redirect(href: str) -> str | None:
    """DuckDuckGo HTML results wrap real URLs behind /l/?uddg=<encoded>."""
    if href.startswith("//duckduckgo.com/l/"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path == "/l/":
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        return target
    return href


def _search_duckduckgo(query: str) -> str | None:
    if _ddg_breaker.open:
        return None
    # Fail fast: a 403/timeout here means "blocked", and retrying the exact
    # same blocked request 2-3 more times within the same call wastes time
    # without ever succeeding.
    resp = get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=8, max_retries=0)
    if resp is None or resp.status_code != 200:
        log.warning("DuckDuckGo search failed for '%s'", query)
        _ddg_breaker.record(success=False)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for link in soup.select("a.result__a"):
        real_url = _valid_result(_clean_ddg_redirect(link.get("href", "")))
        if real_url:
            _ddg_breaker.record(success=True)
            return real_url
    log.warning("DuckDuckGo returned 200 but zero usable results for '%s' - possible bot-challenge page", query)
    _ddg_breaker.record(success=False)
    return None


def _search_bing(query: str) -> str | None:
    if _bing_breaker.open:
        return None
    resp = get("https://www.bing.com/search", params={"q": query}, timeout=8, max_retries=0)
    if resp is None or resp.status_code != 200:
        log.warning("Bing search failed for '%s'", query)
        _bing_breaker.record(success=False)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    # Bing's organic results sit in <li class="b_algo"><h2><a href="...">.
    # This markup isn't a stable contract (unlike an API) and could drift -
    # if this stops finding results, check actual response HTML and update
    # the selector here; the rest of the pipeline degrades gracefully either
    # way (manual review, not a crash).
    for result in soup.select("li.b_algo h2 a"):
        real_url = _valid_result(result.get("href"))
        if real_url:
            _bing_breaker.record(success=True)
            return real_url

    # A 200 status with zero parsed results is NOT the same as a genuine
    # search miss - it's the signature of Bing serving a cookie-consent
    # wall, a bot-challenge page, or a selector that no longer matches
    # current markup. Treating this as a breaker "failure" (not "success")
    # means repeated empty-200 responses correctly open the circuit instead
    # of silently burning through the whole batch getting nothing.
    log.warning(
        "Bing returned 200 but zero usable results for '%s' - likely a consent/"
        "challenge page or a stale selector, not a real empty search", query,
    )
    _bing_breaker.record(success=False)
    return None


# Tried in order; a query only fails if every engine in the chain fails for it.
SEARCH_ENGINES = (_search_duckduckgo, _search_bing)


def search_website(venue_name: str, city: str) -> str | None:
    query = f'{venue_name} {city} bar'
    for search_fn in SEARCH_ENGINES:
        url = search_fn(query)
        if url:
            return url
    return None


def find_website(venue: dict) -> tuple[str | None, str]:
    """Returns (website_url, website_status)."""
    if venue.get("website_url"):
        return venue["website_url"], "FOUND"

    url = search_website(venue["venue_name"], venue["city"])
    if url:
        return url, "FOUND"
    return None, "UNAVAILABLE"


def verify_website_reachable(url: str) -> str:
    """Quick reachability check. Returns FOUND, UNAVAILABLE, or BLOCKED."""
    resp = get(url)
    if resp is None:
        return "UNAVAILABLE"
    if resp.status_code in (403, 429):
        return "BLOCKED"
    if resp.status_code >= 400:
        return "UNAVAILABLE"
    return "FOUND"
