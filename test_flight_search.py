"""Tests for flight_search — focused on FIX 8, the quota-safety memoization.

The scarce resource is the SerpApi free quota (250 searches/MONTH), so the key
guarantees under test are:
  - two identical search_cash_price calls => only ONE underlying HTTP call
  - a different (route/date/cabin) => a second HTTP call
  - failures (SearchFailed) are NOT cached, so a retry hits HTTP again
  - clear_cache() forces a fresh call
Plus a couple of regression guards: HTTPError/RequestException map to SearchFailed
without leaking the API key or request URL, and a single malformed price row does
not abort the whole result set.
"""

import pytest
import requests

import flight_search


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _one_offer_payload(price=250):
    """Minimal but real-shaped Google Flights payload with a single offer."""
    return {
        "best_flights": [
            {
                "price": price,
                "total_duration": 360,
                "flights": [
                    {
                        "airline": "United",
                        "flight_number": "UA 1",
                        "departure_airport": {"id": "SFO", "name": "San Francisco", "time": "2026-09-01 08:00"},
                        "arrival_airport": {"id": "JFK", "name": "New York", "time": "2026-09-01 16:20"},
                        "duration": 360,
                    }
                ],
                "layovers": [],
            }
        ],
        "other_flights": [],
    }


class _Counter:
    """Counts underlying HTTP calls and returns a configurable response/error."""

    def __init__(self, response=None, exc=None):
        self.calls = 0
        self.response = response
        self.exc = exc
        self.last_params = None

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.last_params = kwargs.get("params")
        if self.exc is not None:
            raise self.exc
        return self.response


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Every test starts with a configured key and an empty cache."""
    monkeypatch.setattr(flight_search, "_get_api_key", lambda: "test-key")
    flight_search.clear_cache()
    yield
    flight_search.clear_cache()


def _patch_get(monkeypatch, counter):
    monkeypatch.setattr(flight_search.requests, "get", counter)


def test_identical_calls_hit_http_only_once(monkeypatch):
    counter = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, counter)

    first = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    second = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")

    assert counter.calls == 1  # second call served from cache
    assert first[0].price_usd == 250
    assert second[0].price_usd == 250


def test_cache_key_normalizes_case_and_whitespace(monkeypatch):
    counter = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, counter)

    flight_search.search_cash_price("sfo", "jfk", "2026-09-01", "economy")
    flight_search.search_cash_price("  SFO ", " JFK ", "2026-09-01", " Economy ")

    assert counter.calls == 1


def test_different_route_date_or_cabin_triggers_second_call(monkeypatch):
    counter = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, counter)

    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    flight_search.search_cash_price("SFO", "LAX", "2026-09-01", "ECONOMY")  # route
    flight_search.search_cash_price("SFO", "JFK", "2026-09-02", "ECONOMY")  # date
    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "BUSINESS")  # cabin

    assert counter.calls == 4


def test_max_results_does_not_fragment_cache_and_slices(monkeypatch):
    payload = {
        "best_flights": [
            {
                "price": p,
                "total_duration": 360,
                "flights": [
                    {
                        "airline": "United",
                        "flight_number": f"UA {p}",
                        "departure_airport": {"id": "SFO", "name": "SF", "time": "2026-09-01 08:00"},
                        "arrival_airport": {"id": "JFK", "name": "NY", "time": "2026-09-01 16:20"},
                        "duration": 360,
                    }
                ],
                "layovers": [],
            }
            for p in (300, 100, 200)
        ],
        "other_flights": [],
    }
    counter = _Counter(response=_FakeResp(payload))
    _patch_get(monkeypatch, counter)

    one = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY", max_results=1)
    three = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY", max_results=3)

    assert counter.calls == 1  # same key despite different max_results
    assert [o.price_usd for o in one] == [100.0]  # cheapest first, sliced to 1
    assert [o.price_usd for o in three] == [100.0, 200.0, 300.0]


def test_search_failed_is_not_cached(monkeypatch):
    # First call: transient network failure -> SearchFailed, must NOT be cached.
    failing = _Counter(exc=requests.ConnectionError("boom"))
    _patch_get(monkeypatch, failing)
    with pytest.raises(flight_search.SearchFailed):
        flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert failing.calls == 1

    # Retry now succeeds because the failure was never cached.
    ok = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, ok)
    offers = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert ok.calls == 1
    assert offers[0].price_usd == 250


def test_empty_result_is_not_cached(monkeypatch):
    # No inventory now -> empty list, must NOT be cached so a retry can find
    # newly-available seats.
    empty = _Counter(response=_FakeResp({"best_flights": [], "other_flights": []}))
    _patch_get(monkeypatch, empty)
    assert flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY") == []

    later = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, later)
    offers = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert later.calls == 1  # retried, not stuck on the empty result
    assert offers[0].price_usd == 250


def test_clear_cache_forces_fresh_call(monkeypatch):
    counter = _Counter(response=_FakeResp(_one_offer_payload()))
    _patch_get(monkeypatch, counter)

    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert counter.calls == 1
    flight_search.clear_cache()
    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert counter.calls == 2


def test_http_error_maps_to_search_failed_without_leaking_key(monkeypatch):
    counter = _Counter(response=_FakeResp({}, status_code=429))
    _patch_get(monkeypatch, counter)

    with pytest.raises(flight_search.SearchFailed) as exc:
        flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    msg = str(exc.value)
    assert "test-key" not in msg
    assert "api_key" not in msg
    assert flight_search.SEARCH_URL not in msg


def test_request_exception_maps_to_search_failed_without_leaking_key(monkeypatch):
    counter = _Counter(exc=requests.ConnectionError("https://serpapi.com/search?api_key=test-key"))
    _patch_get(monkeypatch, counter)

    with pytest.raises(flight_search.SearchFailed) as exc:
        flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    msg = str(exc.value)
    assert "test-key" not in msg
    assert flight_search.SEARCH_URL not in msg


def test_malformed_price_row_does_not_abort_result_set(monkeypatch):
    payload = _one_offer_payload(price=250)
    # Inject a bad row (non-numeric price) ahead of the good one.
    bad_row = {
        "price": "not-a-number",
        "flights": [
            {
                "airline": "United",
                "flight_number": "UA 9",
                "departure_airport": {"id": "SFO", "name": "SF", "time": "2026-09-01 08:00"},
                "arrival_airport": {"id": "JFK", "name": "NY", "time": "2026-09-01 16:20"},
                "duration": 360,
            }
        ],
        "layovers": [],
    }
    payload["best_flights"] = [bad_row] + payload["best_flights"]
    counter = _Counter(response=_FakeResp(payload))
    _patch_get(monkeypatch, counter)

    offers = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert [o.price_usd for o in offers] == [250.0]  # bad row skipped, good row kept
