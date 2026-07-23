import pytest

import check_alerts
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
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 0.73 if currency == "CAD" else 1.0)
    alerts = [dict(origin="JFK", dest="CTG", program="Air Canada Aeroplan",
                   cabin="ECONOMY", date="2027-01-27", points=20000, taxes=100.0, currency="CAD")]

    results = evaluate_alerts(alerts)

    assert results[0]["taxes_usd"] == 73.0


def test_fx_rate_falls_back_when_live_lookup_fails(monkeypatch):
    check_alerts._fx_cache.clear()

    def _raise_request_exception(*args, **kwargs):
        raise check_alerts.requests.RequestException("no network")

    monkeypatch.setattr(check_alerts.requests, "get", _raise_request_exception)

    assert check_alerts._fx_rate("CAD") == check_alerts._FX_FALLBACK["CAD"]


def test_evaluate_alerts_reuses_cash_price_for_duplicate_route_date_cabin(monkeypatch):
    calls = []

    def _fake_search(origin, dest, date, cabin, max_results=1):
        calls.append((origin, dest, date, cabin))
        return [_FakeOffer(500.0)]

    monkeypatch.setattr(flight_search, "search_cash_price", _fake_search)
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 1.0)
    alerts = [
        dict(origin="JFK", dest="MAD", program="Program A", cabin="BUSINESS",
             date="2026-11-04", points=40000, taxes=30.0, currency="USD"),
        dict(origin="JFK", dest="MAD", program="Program B", cabin="BUSINESS",
             date="2026-11-04", points=50000, taxes=30.0, currency="USD"),
    ]

    results = evaluate_alerts(alerts)

    assert len(calls) == 1  # only one live lookup for the shared origin/dest/date/cabin
    assert all(r["cash_price"] == 500.0 for r in results)


def test_evaluate_alerts_handles_search_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise flight_search.SearchFailed("boom")

    monkeypatch.setattr(flight_search, "search_cash_price", _raise)
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 1.0)
    alerts = [dict(origin="JFK", dest="XXX", program="Test", cabin="ECONOMY",
                   date="2026-01-01", points=10000, taxes=10.0, currency="USD")]

    results = evaluate_alerts(alerts)

    assert results[0]["verdict"] == "NO CASH PRICE"
    assert results[0]["cash_price"] is None
    # FIX 3: a raised lookup error is a TRANSIENT failure -> priced_ok False (retry).
    assert results[0]["priced_ok"] is False
    assert results[0]["price_error"] == "boom"


def test_evaluate_alerts_no_offers_is_definitive_not_transient(monkeypatch):
    # Empty offers (route simply has no cash fare) is a real answer, not a blip.
    monkeypatch.setattr(flight_search, "search_cash_price",
                        lambda *a, **k: [])
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 1.0)
    alerts = [dict(origin="JFK", dest="XXX", program="Test", cabin="ECONOMY",
                   date="2026-01-01", points=10000, taxes=10.0, currency="USD")]

    results = evaluate_alerts(alerts)

    assert results[0]["verdict"] == "NO CASH PRICE"
    assert results[0]["priced_ok"] is True   # definitive -> won't be retried forever


def test_evaluate_alerts_guards_zero_points(monkeypatch):
    monkeypatch.setattr(flight_search, "search_cash_price",
                        lambda *a, **k: [_FakeOffer(500.0)])
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 1.0)
    alerts = [dict(origin="JFK", dest="MAD", program="Test", cabin="BUSINESS",
                   date="2026-11-04", points=0, taxes=10.0, currency="USD")]

    results = evaluate_alerts(alerts)  # must not raise ZeroDivisionError

    assert results[0]["cpp"] is None
    assert results[0]["priced_ok"] is True


def test_evaluate_alerts_verdict_is_cabin_aware(monkeypatch):
    # Economy at ~1.6c clears the 1.5 economy bar => BOOK (used to be BORDERLINE).
    monkeypatch.setattr(flight_search, "search_cash_price",
                        lambda *a, **k: [_FakeOffer(170.0)])
    monkeypatch.setattr(check_alerts, "_fx_rate", lambda currency: 1.0)
    alerts = [dict(origin="JFK", dest="XXX", program="Test", cabin="ECONOMY",
                   date="2026-01-01", points=10000, taxes=10.0, currency="USD")]

    results = evaluate_alerts(alerts)

    assert results[0]["cpp"] == pytest.approx(1.6)
    assert results[0]["verdict"] == "BOOK"
