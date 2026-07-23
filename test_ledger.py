"""Data-durability tests for ledger.py (fixes 6a, 6b, 6c).

The module resolves BALANCES_PATH / HISTORY_PATH at import time from its own
directory. Every test redirects those module-level constants into tmp_path via
monkeypatch so the user's real balances.json / history.csv are never touched.
"""

import csv
import json
import os

import pytest

import ledger


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the module at temp files and return them."""
    balances = tmp_path / "balances.json"
    history = tmp_path / "history.csv"
    monkeypatch.setattr(ledger, "BALANCES_PATH", str(balances))
    monkeypatch.setattr(ledger, "HISTORY_PATH", str(history))
    return balances, history


# ── Balances round-trip ──────────────────────────────────────────────────
def test_save_load_roundtrip(paths):
    data = {"amex_mr": 120_000, "chase_ur": 45_000, "wf_rewards": 0}
    ledger.save_balances(data)
    assert ledger.load_balances() == data


# ── FIX 6a — atomic write ─────────────────────────────────────────────────
def test_atomic_write_leaves_valid_file(paths):
    balances, _ = paths
    ledger.save_balances({"amex_mr": 100_000})
    # The on-disk file must be valid JSON, and no temp leftovers remain.
    with open(balances) as f:
        assert json.load(f) == {"amex_mr": 100_000}
    leftovers = [p for p in os.listdir(balances.parent) if p.startswith(".balances-")]
    assert leftovers == []


def test_failed_write_preserves_existing_file(paths, monkeypatch):
    balances, _ = paths
    good = {"amex_mr": 100_000, "chase_ur": 50_000}
    ledger.save_balances(good)

    # Simulate a crash during the write (after temp file created, before/at
    # replace). The pre-existing good file must survive intact.
    def boom(*a, **k):
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(ledger.os, "replace", boom)
    with pytest.raises(RuntimeError):
        ledger.save_balances({"amex_mr": 1})  # would-be clobbering write

    # Old data still there and parseable — no truncation, no data loss.
    assert ledger.load_balances() == good
    # Temp file cleaned up despite the failure.
    leftovers = [p for p in os.listdir(balances.parent) if p.startswith(".balances-")]
    assert leftovers == []


# ── FIX 6b — per-key tolerance on load ────────────────────────────────────
def test_one_bad_value_keeps_good_keys(paths):
    balances, _ = paths
    balances.write_text(
        json.dumps(
            {
                "amex_mr": 100_000,
                "chase_ur": "not a number",  # bad
                "wf_rewards": 25_000,
                "citi_typ": 12000.9,  # float, should truncate to 12000
            }
        )
    )
    loaded = ledger.load_balances()
    assert loaded == {
        "amex_mr": 100_000,
        "wf_rewards": 25_000,
        "citi_typ": 12000,
    }
    assert "chase_ur" not in loaded


def test_string_int_values_coerced(paths):
    balances, _ = paths
    balances.write_text(json.dumps({"amex_mr": "100000"}))
    assert ledger.load_balances() == {"amex_mr": 100_000}


# ── FIX 6b — wholesale failure modes ──────────────────────────────────────
def test_missing_file_returns_empty(paths):
    assert ledger.load_balances() == {}


def test_wholesale_corrupt_json_returns_empty(paths):
    balances, _ = paths
    balances.write_text("{ this is not valid json ")
    assert ledger.load_balances() == {}


def test_non_dict_json_returns_empty(paths):
    balances, _ = paths
    balances.write_text(json.dumps([1, 2, 3]))
    assert ledger.load_balances() == {}


# ── FIX 6c — history header robustness ────────────────────────────────────
def _append(pool_key="amex_mr", route="JFK-LHR"):
    ledger.append_history(
        route=route,
        program="Test Program",
        pool_key=pool_key,
        cash_price=1500.0,
        taxes_fees=120.0,
        points_required=60000,
        cpp=2.5,
        avg_simulated_cpp=2.4,
        verdict="REDEEM",
    )


def test_header_written_for_missing_file(paths):
    _append()
    rows = ledger.load_history()
    assert len(rows) == 1
    assert rows[0]["route"] == "JFK-LHR"
    assert list(rows[0].keys()) == ledger.HISTORY_COLUMNS


def test_header_written_for_empty_file(paths):
    _, history = paths
    history.write_text("")  # exists but size 0
    _append()
    rows = ledger.load_history()
    assert len(rows) == 1
    assert rows[0]["route"] == "JFK-LHR"


def test_append_does_not_duplicate_header(paths):
    _, history = paths
    _append(route="JFK-LHR")
    _append(route="SFO-NRT")
    rows = ledger.load_history()
    assert len(rows) == 2
    assert [r["route"] for r in rows] == ["JFK-LHR", "SFO-NRT"]
    # The literal header line must appear exactly once in the raw file.
    raw = history.read_text()
    assert raw.count("route") == 1
