import src.discovery.overpass_source as overpass_source


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {"elements": []}

    def json(self):
        return self._json_data


def test_retries_on_429_and_eventually_succeeds(monkeypatch):
    monkeypatch.setattr(overpass_source.time, "sleep", lambda s: None)
    call_count = {"n": 0}

    def fake_get(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return FakeResponse(status_code=429)
        return FakeResponse(status_code=200, json_data={"elements": []})

    monkeypatch.setattr(overpass_source, "get", fake_get)

    resp = overpass_source._query_overpass_with_retry("fake query", "Berlin")
    assert resp.status_code == 200
    assert call_count["n"] == 3


def test_gives_up_after_max_retries_on_persistent_429(monkeypatch):
    monkeypatch.setattr(overpass_source.time, "sleep", lambda s: None)
    call_count = {"n": 0}

    def always_429(url, **kwargs):
        call_count["n"] += 1
        return FakeResponse(status_code=429)

    monkeypatch.setattr(overpass_source, "get", always_429)

    resp = overpass_source._query_overpass_with_retry("fake query", "Cologne")
    assert resp.status_code == 429
    assert call_count["n"] == overpass_source.OVERPASS_RATE_LIMIT_RETRIES + 1


def test_does_not_retry_on_non_rate_limit_error(monkeypatch):
    monkeypatch.setattr(overpass_source.time, "sleep", lambda s: None)
    call_count = {"n": 0}

    def server_error(url, **kwargs):
        call_count["n"] += 1
        return FakeResponse(status_code=500)

    monkeypatch.setattr(overpass_source, "get", server_error)

    resp = overpass_source._query_overpass_with_retry("fake query", "Frankfurt")
    assert resp.status_code == 500
    assert call_count["n"] == 1  # not a rate-limit code - no point retrying


def test_discover_city_still_returns_empty_gracefully_when_all_retries_exhausted(monkeypatch):
    monkeypatch.setattr(overpass_source.time, "sleep", lambda s: None)
    monkeypatch.setattr(overpass_source, "get", lambda url, **kwargs: FakeResponse(status_code=429))

    result = overpass_source.discover_city("Cologne", tier=1, max_candidates=50)
    assert result == []
