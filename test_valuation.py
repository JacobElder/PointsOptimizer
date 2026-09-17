import pytest

import valuation


def test_compute_cpp_subtracts_taxes_and_guards_zero_points():
    assert valuation.compute_cpp(1000.0, 33.5, 43000) == pytest.approx((1000 - 33.5) / 43000 * 100)
    assert valuation.compute_cpp(100.0, 200.0, 10000) == 0.0
    assert valuation.compute_cpp(500.0, 10.0, 0) is None


def test_verdict_is_cabin_aware():
    assert valuation.verdict_for(1.6, "ECONOMY") == "BOOK"
    assert valuation.verdict_for(1.6, "BUSINESS") == "BORDERLINE"
    assert valuation.verdict_for(2.0, "FIRST") == "BOOK"
    assert valuation.verdict_for(0.9, "BUSINESS") == "SKIP"
    assert valuation.verdict_for(None, "ECONOMY") == "NO CASH PRICE"


def test_fx_rate_falls_back_when_live_lookup_fails(monkeypatch):
    valuation._fx_cache.clear()

    def _raise(*a, **k):
        raise valuation.requests.RequestException("offline")

    monkeypatch.setattr(valuation.requests, "get", _raise)
    assert valuation.fx_rate("CAD") == valuation._FX_FALLBACK["CAD"]
    assert valuation.fx_rate("usd") == 1.0


def test_recorded_fares_reads_deals(tmp_path, monkeypatch):
    p = tmp_path / "deal_log.json"
    p.write_text('{"deals": [{"origin": "JFK", "cash_price": 500}], "pending": []}')
    monkeypatch.setattr(valuation, "RECORDED_FARES_PATH", str(p))
    assert valuation.recorded_fares() == [{"origin": "JFK", "cash_price": 500}]
    monkeypatch.setattr(valuation, "RECORDED_FARES_PATH", str(tmp_path / "missing.json"))
    assert valuation.recorded_fares() == []
