"""Tests for the seats.aero Cached Search wrapper.

Focus: the raw JSON response shape, especially that TotalTaxesRaw (minor
currency units / cents) is converted to dollars so it matches the alert-email
parsing path and the CPP math downstream.
"""

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
    assert o.program == "American"  # unmapped source -> title-cased slug
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
