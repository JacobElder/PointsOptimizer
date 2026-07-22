import pytest

import flight_search
from check_alerts import evaluate_alerts


class _FakeOffer:
    def __init__(self, price_usd):
        self.price_usd = price_usd


def test_evaluate_alerts_computes_cpp_and_verdict(monkeypatch):
    monkeypatch.setattr(
        flight_search, "search_cash_price",
        lambda origin, dest, date, cabin, max_results=1: [_FakeOffer(1000.0)],
    )
    alerts = [dict(origin="JFK", dest="MAD", program="Air France-KLM Flying Blue",
                   cabin="BUSINESS", date="2026-11-04", points=43000, taxes=33.50, currency="USD")]

    results = evaluate_alerts(alerts)

    assert len(results) == 1
    r = results[0]
    assert r["cash_price"] == 1000.0
    assert r["cpp"] == pytest.approx((1000.0 - 33.50) / 43000 * 100)
    assert r["verdict"] == "BOOK"


def test_evaluate_alerts_converts_cad_taxes_to_usd(monkeypatch):
    monkeypatch.setattr(
        flight_search, "search_cash_price",
        lambda origin, dest, date, cabin, max_results=1: [_FakeOffer(500.0)],
    )
    alerts = [dict(origin="JFK", dest="CTG", program="Air Canada Aeroplan",
                   cabin="ECONOMY", date="2027-01-27", points=20000, taxes=100.0, currency="CAD")]

    results = evaluate_alerts(alerts)

    assert results[0]["taxes_usd"] == 73.0


def test_evaluate_alerts_handles_search_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise flight_search.SearchFailed("boom")

    monkeypatch.setattr(flight_search, "search_cash_price", _raise)
    alerts = [dict(origin="JFK", dest="XXX", program="Test", cabin="ECONOMY",
                   date="2026-01-01", points=10000, taxes=10.0, currency="USD")]

    results = evaluate_alerts(alerts)

    assert results[0]["verdict"] == "NO CASH PRICE"
    assert results[0]["cash_price"] is None
