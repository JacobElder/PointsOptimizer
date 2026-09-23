import pytest

import valuation


def test_compute_cpp_subtracts_taxes_and_guards_zero_points():
    assert valuation.compute_cpp(1000.0, 33.5, 43000) == pytest.approx((1000 - 33.5) / 43000 * 100)
    assert valuation.compute_cpp(100.0, 200.0, 10000) == 0.0
    assert valuation.compute_cpp(500.0, 10.0, 0) is None


def test_bars_come_from_each_program_baseline():
    # United points are worth ~1.2c, Alaska ~1.55c, so the same CPP is a standout
    # in one and only ordinary in the other.
    assert valuation.baseline_cpp("United MileagePlus") == 1.2
    assert valuation.great_floor("ECONOMY", "United MileagePlus") == pytest.approx(1.92)
    assert valuation.great_floor("ECONOMY", "Alaska Atmos Rewards") == pytest.approx(2.48)
    assert valuation.verdict_for(2.0, "ECONOMY", "United MileagePlus") == "BOOK"
    assert valuation.verdict_for(2.0, "ECONOMY", "Alaska Atmos Rewards") == "BORDERLINE"
    # Worth less than simply using the points normally.
    assert valuation.verdict_for(1.3, "ECONOMY", "Alaska Atmos Rewards") == "SKIP"
    assert valuation.verdict_for(None, "ECONOMY") == "NO CASH PRICE"


def test_premium_cabins_use_a_higher_baseline():
    """Business redemptions return more per point, so a flat number made business
    bars too easy to clear."""
    econ = valuation.baseline_cpp("United MileagePlus", "ECONOMY")
    biz = valuation.baseline_cpp("United MileagePlus", "BUSINESS")
    assert biz == pytest.approx(econ * valuation.program_values()["business_multiplier"])
    # Published business value wins over the multiplier where we have one.
    assert valuation.baseline_cpp("British Airways Executive Club", "BUSINESS") == 2.2
    assert valuation.great_floor("BUSINESS", "British Airways Executive Club") == pytest.approx(2.75)


def test_unknown_program_falls_back_to_the_default_value():
    assert valuation.baseline_cpp("Made Up Miles") == valuation.program_values()["default_cpp"]


def test_a_cheap_program_cannot_set_a_trivial_bar():
    # GOL Smiles economy baseline 1.0c x 1.6 = 1.6, above the 1.5c economy floor;
    # a program valued at 0.8c would fall to the floor instead.
    assert valuation.great_floor("ECONOMY", "Aeromexico Rewards") == 1.5


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
