"""Estimated taxes for programs seats.aero reports as $0 (award_taxes.py).

A $0 is never real and always flatters the deal, so the estimate matters; and the
table is committed by the daily run, so a bad read must never be overwritten.
"""
import json
import os
from types import SimpleNamespace

import pytest

import award_taxes


def _c(source, cabin, taxes, currency="USD", observed=None):
    c = SimpleNamespace(source=source, cabin=cabin, taxes=taxes, taxes_currency=currency)
    if observed is not None:
        c.taxes_usd_observed = observed
    return c


def test_learn_needs_five_observations_and_takes_the_median():
    cands = [_c("turkish", "business", t) for t in (200, 219, 230, 500, 210)]
    data = award_taxes.learn(cands, {"learned": {}, "curated": {}})
    row = data["learned"]["turkish|BUSINESS"]
    assert (row["usd"], row["n"]) == (219.0, 5)
    thin = award_taxes.learn(cands[:4], {"learned": {}, "curated": {}})
    assert thin["learned"] == {}


def test_learn_ignores_zero_and_converts_currency(monkeypatch):
    import valuation

    def offline(*a, **k):
        raise valuation.requests.ConnectionError("offline")

    monkeypatch.setattr(valuation.requests, "get", offline)  # falls back to the AED peg, 0.2723
    cands = [_c("emirates", "BUSINESS", 1000, "AED") for _ in range(5)] + [_c("emirates", "BUSINESS", 0)] * 3
    data = award_taxes.learn(cands, {})
    assert data["learned"]["emirates|BUSINESS"]["usd"] == 272.3
    assert data["learned"]["emirates|BUSINESS"]["n"] == 5  # the $0 rows don't count


def test_learn_prefers_an_observed_usd_figure_and_survives_a_bad_row():
    cands = [_c("united", "ECONOMY", "garbage", observed=5.6) for _ in range(5)] + [_c("united", "ECONOMY", "bad")]
    data = award_taxes.learn(cands, {"learned": {}})
    assert data["learned"]["united|ECONOMY"]["usd"] == 5.6 and data["learned"]["united|ECONOMY"]["n"] == 5


def test_learn_keeps_existing_entries_it_did_not_see_again():
    old = {"learned": {"qatar|FIRST": {"usd": 150.0, "n": 9, "updated": "2026-09-01"}}, "curated": {}}
    data = award_taxes.learn([_c("turkish", "BUSINESS", 219)] * 5, old)
    assert data["learned"]["qatar|FIRST"]["usd"] == 150.0 and "turkish|BUSINESS" in data["learned"]


def test_estimate_prefers_learned_then_curated_and_never_borrows_another_cabin():
    data = {"learned": {"virginatlantic|ECONOMY": {"usd": 5.6, "n": 40}},
            "curated": {"virginatlantic|BUSINESS": {"usd": 640.0, "note": "Upper Class surcharge"},
                        "virginatlantic|ECONOMY": {"usd": 99.0}}}
    usd, why = award_taxes.estimate("virginatlantic", "economy", data)
    assert usd == 5.6 and why == "typical for Virgin Atlantic Flying Club in this cabin, from 40 awards we've seen"
    assert award_taxes.estimate("virginatlantic", "BUSINESS", data) == (640.0, "Upper Class surcharge")
    assert award_taxes.estimate("virginatlantic", "FIRST", data) == (None, "no estimate available")
    curated_no_note = {"curated": {"aeroplan|ECONOMY": {"usd": 40.0}}}
    assert award_taxes.estimate("aeroplan", "ECONOMY", curated_no_note) == (
        40.0, "published figure for this program")


def test_load_missing_file_is_an_empty_table_and_save_round_trips():
    assert award_taxes.load() == {"learned": {}, "curated": {}}
    award_taxes.save({"learned": {"a|ECONOMY": {"usd": 1.0, "n": 5}}, "curated": {}})
    assert award_taxes.load(strict=True)["learned"]["a|ECONOMY"]["usd"] == 1.0
    assert award_taxes.estimate("a", "ECONOMY")[0] == 1.0  # reads the saved table by default


def test_unreadable_table_raises_in_strict_mode_and_stays_on_disk():
    with open(award_taxes.TAXES_PATH, "w") as f:
        f.write('{"curated": {"turkish|BUSINESS": ')  # truncated
    with pytest.raises(award_taxes.Unreadable):
        award_taxes.load(strict=True)
    assert award_taxes.load() == {"learned": {}, "curated": {}}
    with open(award_taxes.TAXES_PATH) as f:
        assert f.read() == '{"curated": {"turkish|BUSINESS": '  # untouched


def test_failed_save_keeps_the_old_table_and_cleans_up(monkeypatch):
    award_taxes.save({"learned": {}, "curated": {"x|ECONOMY": {"usd": 9.0}}})

    def boom(*a, **k):
        raise RuntimeError("crash")

    monkeypatch.setattr(award_taxes.os, "replace", boom)
    with pytest.raises(RuntimeError):
        award_taxes.save({"learned": {}, "curated": {}})
    with open(award_taxes.TAXES_PATH) as f:
        assert json.load(f)["curated"] == {"x|ECONOMY": {"usd": 9.0}}
    assert [p for p in os.listdir(os.path.dirname(award_taxes.TAXES_PATH)) if p.startswith(".taxes-")] == []


def test_tests_never_write_the_repo_tax_table():
    real_dir = os.path.dirname(os.path.abspath(award_taxes.__file__))
    assert os.path.dirname(award_taxes.TAXES_PATH) != real_dir
