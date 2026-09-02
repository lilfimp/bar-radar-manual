"""Small wrapper around requests with retries, timeout and a politeness delay.

Kept deliberately dependency-light (requests only) since Playwright is only
pulled in by the enrichment step for JS-heavy sites, and only as a fallback.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from src.utils.config import settings
from src.utils.logging_utils import get_logger

log = get_logger(__name__)

_last_request_time_by_host: dict[str, float] = {}


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


def _respect_delay(url: str) -> None:
    cfg = settings()["http"]
    delay = cfg["request_delay_seconds"]
    host = _host(url)
    last = _last_request_time_by_host.get(host, 0.0)
    elapsed = time.time() - last
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_time_by_host[host] = time.time()


def get(url: str, **kwargs) -> Optional[requests.Response]:
    """GET with retries. Returns None (never raises) on failure so callers
    can treat network failure as a normal pipeline outcome (WEBSITE_UNAVAILABLE).

    Only genuinely transient failures (timeouts) are retried. DNS failures,
    connection-refused, and "network unreachable" are deterministic - the
    same request will fail identically on attempt 2 and 3, so retrying them
    just burns the batch's time budget for nothing. Accepts an optional
    `max_retries` kwarg to override the config default per-call (e.g. fail
    fast on a fragile fallback endpoint like DuckDuckGo)."""
    cfg = settings()["http"]
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", cfg["user_agent"])
    timeout = kwargs.pop("timeout", cfg["timeout_seconds"])
    max_retries = kwargs.pop("max_retries", cfg["max_retries"])

    for attempt in range(max_retries + 1):
        _respect_delay(url)
        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                **kwargs,
            )
            if resp.status_code == 403 or resp.status_code == 429:
                log.warning("Blocked (%s) on %s", resp.status_code, url)
                return resp  # let caller decide BLOCKED vs retry
            return resp
        except requests.Timeout as exc:
            # Genuinely transient - the connection might succeed next time.
            log.warning("Timeout (attempt %d/%d) for %s: %s", attempt + 1, max_retries + 1, url, exc)
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))
        except requests.ConnectionError as exc:
            # DNS failure, connection refused, network unreachable - retrying
            # the identical request will not produce a different outcome.
            log.warning("Connection failed (not retrying) for %s: %s", url, exc)
            return None
        except requests.RequestException as exc:
            log.warning("Request failed (attempt %d/%d) for %s: %s", attempt + 1, max_retries + 1, url, exc)
            if attempt < max_retries:
                time.sleep(0.5 * (attempt + 1))
    return None
