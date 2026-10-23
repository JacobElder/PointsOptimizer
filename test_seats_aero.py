"""Tests for the seats.aero Cached Search wrapper.

Focus: the raw JSON response shape, especially that TotalTaxesRaw (minor
currency units / cents) is converted to dollars so it matches the alert-email
parsing path and the CPP math downstream.
"""

import pytest
import requests

import seats_aero


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _patch(monkeypatch, payload, status_code=200):
    monkeypatch.setattr(seats_aero, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(
        seats_aero.requests, "get",
        lambda *a, **k: _FakeResp(payload, status_code),
    )


# One real-shaped Cached Search row: JFK-LHR business, $733.50 in taxes.
_ROW = {
    "Source": "american",
    "Date": "2026-09-01",
    "Route": {"OriginAirport": "JFK", "DestinationAirport": "LHR"},
    "JAvailable": True,
    "JMileageCostRaw": 57500,
    "JTotalTaxesRaw": 73350,  # cents => $733.50
    "TaxesCurrency": "USD",
    "JRemainingSeatsRaw": 4,
    "JAirlines": "American",
    "JDirectRaw": True,
}


def test_total_taxes_raw_is_converted_from_cents_to_dollars(monkeypatch):
    _patch(monkeypatch, {"data": [_ROW]})

    offers = seats_aero.search_award_availability("JFK", "LHR", "2026-09-01", cabin="BUSINESS")

    assert len(offers) == 1
    o = offers[0]
    assert o.points == 57500
    assert o.taxes_fees == 733.50  # NOT 73350.0
    assert o.taxes_currency == "USD"
    assert o.program == "American Airlines AAdvantage"  # mapped since 2026-09-16
    assert o.direct is True


def test_unavailable_cabin_row_is_filtered_out(monkeypatch):
    row = {**_ROW, "JAvailable": False}
    _patch(monkeypatch, {"data": [row]})

    offers = seats_aero.search_award_availability("JFK", "LHR", "2026-09-01", cabin="BUSINESS")

    assert offers == []


def test_429_raises_search_failed_with_quota_message(monkeypatch):
    _patch(monkeypatch, {"data": []}, status_code=429)

    try:
        seats_aero.search_award_availability("JFK", "LHR", "2026-09-01", cabin="BUSINESS")
        assert False, "expected SearchFailed"
    except seats_aero.SearchFailed as e:
        assert "quota" in str(e).lower()


def _raise(exc):
    def get(*a, **k):
        raise exc
    return get


@pytest.mark.parametrize("cents, dollars", [(0, 0.0), (560, 5.60), (1, 0.01), (219_00, 219.0)])
def test_taxes_are_always_cents(monkeypatch, cents, dollars):
    _patch(monkeypatch, {"data": [{**_ROW, "JTotalTaxesRaw": cents}]})
    assert seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")[0].taxes_fees == dollars


def test_other_cabins_read_their_own_prefixed_fields(monkeypatch):
    row = {"Source": "flyingblue", "Date": "2026-10-02", "Route": {},
           "WAvailable": True, "WMileageCostRaw": 41000, "WTotalTaxesRaw": 18890, "WRemainingSeatsRaw": 1,
           "WAirlines": "AF", "JAvailable": True, "JMileageCostRaw": 90000}
    _patch(monkeypatch, {"data": [row]})
    o = seats_aero.search_award_availability("jfk", "cdg", "2026-10-02", cabin="premium_economy")[0]
    assert (o.points, o.taxes_fees, o.cabin, o.remaining_seats) == (41000, 188.90, "PREMIUM_ECONOMY", 1)
    # Missing route/currency fields fall back to the query, not blanks.
    assert (o.origin, o.destination, o.taxes_currency, o.direct) == ("JFK", "CDG", "USD", False)
    assert o.program == "Air France-KLM Flying Blue"


def test_results_are_cheapest_first_and_capped(monkeypatch):
    rows = [{**_ROW, "JMileageCostRaw": p} for p in (90000, 45000, 70000, 60000)]
    _patch(monkeypatch, {"data": rows})
    offers = seats_aero.search_award_availability("JFK", "LHR", "2026-09-01", max_results=3)
    assert [o.points for o in offers] == [45000, 60000, 70000]


def test_unmapped_source_keeps_its_slug_title_cased(monkeypatch):
    _patch(monkeypatch, {"data": [{**_ROW, "Source": "air_lingus"}]})
    o = seats_aero.search_award_availability("JFK", "DUB", "2026-09-01")[0]
    assert o.program == "Air Lingus" and o.known_partner is False


def test_request_sends_the_key_header_and_one_day_range(monkeypatch):
    seen = {}

    def get(url, headers, params, timeout):
        seen.update(url=url, headers=headers, params=params, timeout=timeout)
        return _FakeResp({"data": []})

    monkeypatch.setattr(seats_aero, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(seats_aero.requests, "get", get)
    assert seats_aero.search_award_availability("jfk", "lhr", "2026-09-01") == []
    assert seen["headers"] == {"Partner-Authorization": "test-key"}
    assert seen["params"]["origin_airport"] == "JFK" and seen["params"]["end_date"] == "2026-09-01"
    assert seen["timeout"] == 20


def test_http_error_message_names_the_status_but_never_the_key(monkeypatch):
    _patch(monkeypatch, {"data": []}, status_code=401)
    with pytest.raises(seats_aero.SearchFailed) as e:
        seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")
    assert "HTTP 401" in str(e.value) and "test-key" not in str(e.value)


@pytest.mark.parametrize("exc, word", [(requests.Timeout("t"), "Timeout"),
                                       (requests.ConnectionError("c"), "ConnectionError")])
def test_network_errors_become_search_failed(monkeypatch, exc, word):
    monkeypatch.setattr(seats_aero, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(seats_aero.requests, "get", _raise(exc))
    with pytest.raises(seats_aero.SearchFailed, match=word):
        seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")


def test_unreadable_json_becomes_search_failed(monkeypatch):
    class _Bad(_FakeResp):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(seats_aero, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(seats_aero.requests, "get", lambda *a, **k: _Bad({}))
    with pytest.raises(seats_aero.SearchFailed, match="unreadable"):
        seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")


def test_no_key_raises_not_configured_before_any_request(monkeypatch):
    monkeypatch.setattr(seats_aero.requests, "get", _raise(AssertionError("must not be called")))
    assert seats_aero.is_configured() is False  # conftest: env-only key, unset
    with pytest.raises(seats_aero.NotConfigured):
        seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    assert seats_aero.is_configured() is True


def test_real_key_lookup_reads_env_then_streamlit_secrets(monkeypatch):
    """conftest replaces _get_api_key; load a private copy of the module to test the real one
    without disturbing the shared module (other modules hold its exception classes)."""
    import importlib.util
    import sys
    import types

    spec = importlib.util.spec_from_file_location("_seats_aero_copy", seats_aero.__file__)
    copy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_seats_aero_copy", copy)  # dataclasses look the module up
    spec.loader.exec_module(copy)
    monkeypatch.setenv("SEATS_AERO_API_KEY", "from-env")
    assert copy._get_api_key() == "from-env"
    monkeypatch.delenv("SEATS_AERO_API_KEY")
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={"SEATS_AERO_API_KEY": "s"}))
    assert copy._get_api_key() == "s"
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))
    with pytest.raises(copy.NotConfigured):
        copy._get_api_key()


def test_a_null_seat_count_does_not_crash_the_search(monkeypatch):
    _patch(monkeypatch, {"data": [{**_ROW, "JRemainingSeatsRaw": None}]})
    assert seats_aero.search_award_availability("JFK", "LHR", "2026-09-01")[0].remaining_seats == 0
