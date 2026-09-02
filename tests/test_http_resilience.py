import requests

import src.enrichment.website_finder as website_finder
import src.utils.http_utils as http_utils


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def test_circuit_breaker_opens_after_threshold_and_reports_open():
    breaker = website_finder.CircuitBreaker("Test", threshold=5)
    for _ in range(4):
        breaker.record(success=False)
        assert breaker.open is False
    breaker.record(success=False)
    assert breaker.open is True


def test_circuit_breaker_resets_on_success():
    breaker = website_finder.CircuitBreaker("Test", threshold=5)
    for _ in range(4):
        breaker.record(success=False)
    breaker.record(success=True)
    assert breaker.consecutive_failures == 0
    assert breaker.open is False


def test_duckduckgo_circuit_breaker_stops_calling_get_once_open(monkeypatch):
    website_finder.reset_ddg_circuit_breaker()
    call_count = {"n": 0}

    def always_blocked(url, **kwargs):
        call_count["n"] += 1
        return FakeResponse(status_code=403)

    monkeypatch.setattr(website_finder, "get", always_blocked)

    threshold = website_finder._ddg_breaker.threshold
    for _ in range(threshold):
        website_finder._search_duckduckgo("some query")

    assert call_count["n"] == threshold
    assert website_finder._ddg_breaker.open is True

    # Circuit is open - further calls must NOT hit the network at all.
    website_finder._search_duckduckgo("another query")
    assert call_count["n"] == threshold  # unchanged

    website_finder.reset_ddg_circuit_breaker()


def test_search_website_falls_through_to_bing_when_duckduckgo_fails(monkeypatch):
    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()

    def fake_get(url, **kwargs):
        if "duckduckgo" in url:
            return FakeResponse(status_code=403)
        if "bing" in url:
            html = '<li class="b_algo"><h2><a href="https://realbar.de">Real Bar</a></h2></li>'
            return FakeResponse(status_code=200, text=html)
        return FakeResponse(status_code=404)

    monkeypatch.setattr(website_finder, "get", fake_get)

    result = website_finder.search_website("Real Bar", "Berlin")
    assert result == "https://realbar.de"

    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()


def test_search_website_returns_none_when_all_engines_fail(monkeypatch):
    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()

    monkeypatch.setattr(website_finder, "get", lambda url, **kwargs: FakeResponse(status_code=403))

    result = website_finder.search_website("Nowhere Bar", "Berlin")
    assert result is None

    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()


def test_bing_200_with_zero_parsed_results_counts_as_failure_not_silent_success(monkeypatch):
    """Regression test: a 200 response with no matching result links (e.g. a
    consent/bot-challenge page instead of real results) must be treated as a
    breaker failure, not silently recorded as success. Otherwise the circuit
    never opens even though every single query is coming back empty."""
    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()

    def empty_200(url, **kwargs):
        if "duckduckgo" in url:
            return FakeResponse(status_code=403)
        if "bing" in url:
            return FakeResponse(status_code=200, text="<html><body>consent wall</body></html>")
        return FakeResponse(status_code=404)

    monkeypatch.setattr(website_finder, "get", empty_200)

    for _ in range(website_finder._bing_breaker.threshold):
        result = website_finder.search_website("Some Bar", "Berlin")
        assert result is None

    assert website_finder._bing_breaker.open is True
    assert website_finder._bing_breaker.consecutive_failures >= website_finder._bing_breaker.threshold

    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()


def test_ddg_200_with_zero_parsed_results_counts_as_failure_not_silent_success(monkeypatch):
    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()

    def empty_200(url, **kwargs):
        if "duckduckgo" in url:
            return FakeResponse(status_code=200, text="<html><body>no results here</body></html>")
        return FakeResponse(status_code=403)

    monkeypatch.setattr(website_finder, "get", empty_200)

    for _ in range(website_finder._ddg_breaker.threshold):
        result = website_finder.search_website("Some Bar", "Berlin")
        assert result is None

    assert website_finder._ddg_breaker.open is True

    website_finder.reset_ddg_circuit_breaker()
    website_finder.reset_bing_circuit_breaker()


def test_connection_error_does_not_retry(monkeypatch):
    call_count = {"n": 0}

    def raise_connection_error(*args, **kwargs):
        call_count["n"] += 1
        raise requests.ConnectionError("Name or service not known")

    monkeypatch.setattr(http_utils.requests, "get", raise_connection_error)
    monkeypatch.setattr(http_utils, "_respect_delay", lambda url: None)

    result = http_utils.get("http://doesnotexist.example")

    assert result is None
    assert call_count["n"] == 1  # not retried - a DNS failure won't change on retry


def test_timeout_is_retried(monkeypatch):
    call_count = {"n": 0}

    def raise_timeout(*args, **kwargs):
        call_count["n"] += 1
        raise requests.Timeout("Connection timed out")

    monkeypatch.setattr(http_utils.requests, "get", raise_timeout)
    monkeypatch.setattr(http_utils, "_respect_delay", lambda url: None)
    monkeypatch.setattr(http_utils.time, "sleep", lambda s: None)

    result = http_utils.get("http://slow.example", max_retries=2)

    assert result is None
    assert call_count["n"] == 3  # initial attempt + 2 retries


def test_max_retries_override_is_respected(monkeypatch):
    call_count = {"n": 0}

    def raise_timeout(*args, **kwargs):
        call_count["n"] += 1
        raise requests.Timeout("Connection timed out")

    monkeypatch.setattr(http_utils.requests, "get", raise_timeout)
    monkeypatch.setattr(http_utils, "_respect_delay", lambda url: None)
    monkeypatch.setattr(http_utils.time, "sleep", lambda s: None)

    http_utils.get("http://slow.example", max_retries=0)

    assert call_count["n"] == 1  # no retries when explicitly overridden to 0
