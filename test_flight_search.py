"""Tests for flight_search.search_cash_price: memoization and failure handling.

The guarantees under test:
  - two identical search_cash_price calls => only ONE underlying Google lookup
  - a different (route/date/cabin) => a second lookup
  - failures (SearchFailed) and empty answers are NOT cached, so a retry looks again
  - clear_cache() forces a fresh lookup
  - any scraper error surfaces as SearchFailed; no fast-flights => NotConfigured
The Google Flights page parser itself is tested in test_cash_quotes.py.
"""

import pytest

import flight_search


def _offer(price=250.0):
    seg = flight_search.FlightSegment("United", "UA 1", "SFO", "San Francisco", "2026-09-01 08:00",
                                      "JFK", "New York", "2026-09-01 16:20", 360, "UA")
    return flight_search.FlightOffer(price_usd=price, cabin="ECONOMY", total_duration_minutes=360,
                                     segments=[seg], layovers=[])


class _Counter:
    """Stands in for _fetch_offers_fast_flights: counts calls, returns offers or raises."""

    def __init__(self, offers=None, exc=None):
        self.calls = 0
        self.offers = [_offer()] if offers is None else offers
        self.exc = exc

    def __call__(self, origin, destination, departure_date, cabin):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return list(self.offers)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Every test starts with fast-flights available and an empty cache."""
    monkeypatch.setattr(flight_search, "fast_flights_available", lambda: True)
    flight_search.clear_cache()
    yield
    flight_search.clear_cache()


def _patch(monkeypatch, counter):
    monkeypatch.setattr(flight_search, "_fetch_offers_fast_flights", counter)


def test_identical_calls_look_up_only_once(monkeypatch):
    counter = _Counter()
    _patch(monkeypatch, counter)

    first = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    second = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")

    assert counter.calls == 1  # second call served from cache
    assert first[0].price_usd == 250
    assert second[0].price_usd == 250


def test_cache_key_normalizes_case_and_whitespace(monkeypatch):
    counter = _Counter()
    _patch(monkeypatch, counter)

    flight_search.search_cash_price("sfo", "jfk", "2026-09-01", "economy")
    flight_search.search_cash_price("  SFO ", " JFK ", "2026-09-01", " Economy ")

    assert counter.calls == 1


def test_different_route_date_or_cabin_triggers_second_lookup(monkeypatch):
    counter = _Counter()
    _patch(monkeypatch, counter)

    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    flight_search.search_cash_price("SFO", "LAX", "2026-09-01", "ECONOMY")  # route
    flight_search.search_cash_price("SFO", "JFK", "2026-09-02", "ECONOMY")  # date
    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "BUSINESS")  # cabin

    assert counter.calls == 4


def test_max_results_does_not_fragment_cache_and_slices(monkeypatch):
    counter = _Counter(offers=[_offer(100.0), _offer(200.0), _offer(300.0)])
    _patch(monkeypatch, counter)

    one = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY", max_results=1)
    three = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY", max_results=3)

    assert counter.calls == 1  # same key despite different max_results
    assert [o.price_usd for o in one] == [100.0]
    assert [o.price_usd for o in three] == [100.0, 200.0, 300.0]


def test_failure_is_not_cached(monkeypatch):
    failing = _Counter(exc=flight_search.SearchFailed("no results payload"))
    _patch(monkeypatch, failing)
    with pytest.raises(flight_search.SearchFailed):
        flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert failing.calls == 1

    ok = _Counter()
    _patch(monkeypatch, ok)
    offers = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert ok.calls == 1
    assert offers[0].price_usd == 250


def test_empty_result_is_not_cached(monkeypatch):
    empty = _Counter(offers=[])
    _patch(monkeypatch, empty)
    assert flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY") == []

    later = _Counter()
    _patch(monkeypatch, later)
    offers = flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert later.calls == 1  # retried, not stuck on the empty result
    assert offers[0].price_usd == 250


def test_clear_cache_forces_fresh_lookup(monkeypatch):
    counter = _Counter()
    _patch(monkeypatch, counter)

    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    flight_search.clear_cache()
    flight_search.search_cash_price("SFO", "JFK", "2026-09-01", "ECONOMY")
    assert counter.calls == 2


def test_scraper_error_becomes_search_failed(monkeypatch):
    # Google changing its page, a network error, a block: callers only handle SearchFailed.
    _patch(monkeypatch, _Counter(exc=RuntimeError("google changed")))
    with pytest.raises(flight_search.SearchFailed, match="RuntimeError"):
        flight_search.search_cash_price("SFO", "JFK", "2026-12-03")


def test_without_fast_flights_it_says_so(monkeypatch):
    counter = _Counter()
    _patch(monkeypatch, counter)
    monkeypatch.setattr(flight_search, "fast_flights_available", lambda: False)
    with pytest.raises(flight_search.NotConfigured):
        flight_search.search_cash_price("SFO", "JFK", "2026-12-04")
    assert counter.calls == 0
