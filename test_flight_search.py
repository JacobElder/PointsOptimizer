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


# ── Page parser, pauses and the round-trip lookup ─────────────────────────
import json
import sys

_REAL_AVAILABLE = flight_search.fast_flights_available  # captured before the fixture replaces it


def _seg(o, d, code, num, dep=(10, 0), arr=(18, 0), day=(2026, 11, 12), arr_day=None, minutes=480):
    return ([None, None, None, o, f"{o} airport", f"{d} airport", d, None, list(dep), None, list(arr), minutes]
            + [None] * 8 + [list(day), list(arr_day or day), [code, num, None, f"{code} Air"]])


def _page(best=(), other=()):
    """Google's ds:1 payload: rows are (price or None, [segments])."""
    def row(price, segs):
        return [["XX", ["Fallback Air"], segs], [[None, price]] if price is not None else []]
    payload = [None, None, [[row(*r) for r in best]], [[row(*r) for r in other]]]
    return ('<script class="ds:1">AF_initDataCallback({key: "ds:1", data:'
            + json.dumps(payload) + ', sideChannel: {}});</script>')


def test_parser_layover_minutes_and_total_duration():
    html = _page(other=[(812, [_seg("JFK", "KEF", "FI", "614", dep=(20, 0), arr=(6, 0), minutes=360),
                               _seg("KEF", "ZRH", "FI", "568", dep=(7, 30), arr=(13, 0), minutes=210,
                                    day=(2026, 11, 13))])])
    # The first leg lands on the 13th at 06:00 (arrival date), the next leaves 07:30.
    html = html.replace('[2026, 11, 12], [2026, 11, 12], ["FI", "614"', '[2026, 11, 12], [2026, 11, 13], ["FI", "614"')
    (o,) = flight_search._parse_google_flights_html(html, "ECONOMY")
    assert o.layovers[0].airport == "KEF" and o.layovers[0].duration_minutes == 90
    assert o.total_duration_minutes == 360 + 210 + 90
    assert (o.origin, o.destination, o.stops, o.airline) == ("JFK", "ZRH", 1, "FI Air")
    assert (o.departure_time, o.arrival_time) == ("2026-11-12 20:00", "2026-11-13 13:00")


def test_parser_unreadable_times_give_a_zero_layover_not_a_crash():
    html = _page(other=[(500, [_seg("JFK", "BOS", "B6", "1", arr=(25, 0)), _seg("BOS", "LIS", "TP", "2")])])
    (o,) = flight_search._parse_google_flights_html(html, "ECONOMY")
    assert o.layovers[0].duration_minutes == 0


def test_parser_overlapping_legs_clamp_the_layover_at_zero():
    html = _page(other=[(500, [_seg("JFK", "BOS", "B6", "1", arr=(12, 0)),
                               _seg("BOS", "LIS", "TP", "2", dep=(11, 0))])])
    (o,) = flight_search._parse_google_flights_html(html, "ECONOMY")
    assert o.layovers[0].duration_minutes == 0


def test_parser_skips_zero_price_empty_itineraries_and_missing_prices():
    html = _page(best=[(0, [_seg("JFK", "LIS", "TP", "1")]),
                       (None, [_seg("JFK", "LIS", "TP", "2")]),
                       (650, [])],
                 other=[(700, [_seg("JFK", "LIS", "TP", "3")])])
    offers = flight_search._parse_google_flights_html(html, "ECONOMY")
    assert [(o.price_usd, o.segments[0].flight_number) for o in offers] == [(700.0, "TP 3")]


def test_parser_missing_flight_ident_falls_back_to_the_row_airline():
    seg = _seg("JFK", "LIS", "TP", "1")
    seg[22] = None
    html = _page(other=[(700, [seg])])
    (o,) = flight_search._parse_google_flights_html(html, "ECONOMY")
    assert (o.airline, o.segments[0].flight_number, o.carrier_codes) == ("Fallback Air", "", [])


def test_parser_no_flights_found_is_an_empty_list_and_a_blocked_page_raises():
    no_flights = ('<script class="ds:1">AF_initDataCallback({key: "ds:1", data:[1], '
                  'errorHasStatus: true, sideChannel: {}});</script>')
    assert flight_search._parse_google_flights_html(no_flights, "ECONOMY") == []
    with pytest.raises(flight_search.SearchFailed, match="no results payload"):
        flight_search._parse_google_flights_html("<html>captcha</html>", "ECONOMY")


def test_offer_with_no_segments_has_safe_defaults():
    o = flight_search.FlightOffer(price_usd=1.0, cabin="ECONOMY", total_duration_minutes=0, segments=[], layovers=[])
    assert (o.airline, o.origin, o.destination, o.departure_time, o.arrival_time, o.stops) == (
        "Unknown", "", "", "", "", 0)


def test_fast_flights_available_respects_the_kill_switch_and_import_errors(monkeypatch):
    monkeypatch.setattr(flight_search, "FAST_FLIGHTS_DISABLED", True)
    assert _REAL_AVAILABLE() is False
    monkeypatch.setattr(flight_search, "FAST_FLIGHTS_DISABLED", False)
    monkeypatch.setitem(sys.modules, "fast_flights", None)  # import raises ImportError
    assert _REAL_AVAILABLE() is False


def _stub_pages(monkeypatch, pages):
    sleeps, pages = [], list(pages)

    def fetch(_query):
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(flight_search, "_fetch_html", fetch)
    monkeypatch.setattr(flight_search, "_sleep", sleeps.append)
    flight_search.reset_backoff(enabled=True)
    return sleeps


def test_round_trip_without_fast_flights_raises_search_failed(monkeypatch):
    monkeypatch.setattr(flight_search, "fast_flights_available", lambda: False)
    with pytest.raises(flight_search.SearchFailed, match="needs fast-flights"):
        flight_search.search_round_trip_offers("JFK", "LIS", "2026-11-12", "2026-11-19")


def test_round_trip_blocked_page_and_scraper_error_both_raise_search_failed(monkeypatch):
    _stub_pages(monkeypatch, ["<html>blocked</html>", KeyError("x")])
    with pytest.raises(flight_search.SearchFailed, match="no results payload"):
        flight_search.search_round_trip_offers("JFK", "LIS", "2026-11-12", "2026-11-19")
    with pytest.raises(flight_search.SearchFailed, match="Round-trip lookup failed \\(KeyError\\)"):
        flight_search.search_round_trip_offers("JFK", "LIS", "2026-11-12", "2026-11-19")


def test_round_trip_returns_cheapest_total_first_and_logs_it(monkeypatch):
    _stub_pages(monkeypatch, [_page(other=[(1900, [_seg("JFK", "LIS", "TP", "1")]),
                                           (1400, [_seg("JFK", "LIS", "A3", "2")])])])
    offers = flight_search.search_round_trip_offers(" jfk", "lis ", "2026-11-12", "2026-11-19", "business")
    assert [o.price_usd for o in offers] == [1400.0, 1900.0] and offers[0].cabin == "BUSINESS"
    assert [(k, ok) for _, k, ok in flight_search.lookup_log()] == [("round-trip", True)]


def test_backoff_stats_count_pauses_and_seconds(monkeypatch):
    sleeps = _stub_pages(monkeypatch, ['<script class="ds:1">AF_initDataCallback({data:[null], '
                                       'errorHasStatus: true, x});</script>'] * 3)
    for _ in range(3):
        flight_search._fetch_offers_fast_flights("JFK", "LIS", "2026-11-12", "ECONOMY")
    assert sleeps == [3.0, 6.0]
    assert flight_search.backoff_stats() == {"pauses": 2, "waited_s": 9.0}
    flight_search.reset_backoff(enabled=False)
    assert flight_search.backoff_stats() == {"pauses": 0, "waited_s": 0.0} and flight_search.lookup_log() == []
