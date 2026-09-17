"""Data-durability tests for ledger.py (fixes 6a, 6b, 6c).

The module resolves BALANCES_PATH at import time from its own directory. Every
test redirects it into tmp_path via monkeypatch so the user's real balances.json
is never touched.
"""

import json
import os

import pytest

import ledger


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the module at temp files and return them."""
    balances = tmp_path / "balances.json"
    monkeypatch.setattr(ledger, "BALANCES_PATH", str(balances))
    return balances, None


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


def test_program_balances_roundtrip_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "PROGRAM_BALANCES_PATH", str(tmp_path / "program_balances.json"))
    monkeypatch.delenv("PROGRAM_BALANCES", raising=False)
    assert ledger.load_program_balances() == {}
    ledger.save_program_balances({"JetBlue TrueBlue": 22516, "United MileagePlus": 0})
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 22516}
    monkeypatch.setenv("PROGRAM_BALANCES", '{"JetBlue TrueBlue": 30000, "Alaska Atmos Rewards": "5000"}')
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 30000, "Alaska Atmos Rewards": 5000}
    monkeypatch.setenv("PROGRAM_BALANCES", "not json")
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 22516}


class _FakeGitHub:
    """Minimal in-memory GitHub Gists API."""

    def __init__(self):
        self.gists = {}

    def __call__(self, method, url, **kw):
        path = url.replace("https://api.github.com", "")
        body = kw.get("json") or {}
        if method == "GET" and path == "/gists":
            data = [{"id": gid, "files": {n: {} for n in g}} for gid, g in self.gists.items()]
        elif method == "POST" and path == "/gists":
            gid = f"g{len(self.gists) + 1}"
            self.gists[gid] = {n: f["content"] for n, f in body["files"].items()}
            data = {"id": gid}
        elif method == "PATCH":
            gid = path.rsplit("/", 1)[-1]
            self.gists[gid].update({n: f["content"] for n, f in body["files"].items()})
            data = {"id": gid}
        else:  # GET /gists/{id}
            gid = path.rsplit("/", 1)[-1]
            data = {"files": {n: {"content": c} for n, c in self.gists[gid].items()}}

        class R:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return data

        return R()


def test_gist_is_seeded_from_local_files_then_shared(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "BALANCES_PATH", str(tmp_path / "balances.json"))
    monkeypatch.setattr(ledger, "PROGRAM_BALANCES_PATH", str(tmp_path / "program_balances.json"))
    ledger.save_balances({"chase_ur": 137000})
    ledger.save_program_balances({"JetBlue TrueBlue": 22516})

    fake = _FakeGitHub()
    monkeypatch.setattr(ledger.requests, "request", fake)
    monkeypatch.setattr(ledger, "GIST_DISABLED", False)
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setenv("GIST_TOKEN", "t")

    assert ledger.load_balances() == {"chase_ur": 137000}  # seeds the Gist from local files
    assert len(fake.gists) == 1

    assert ledger.save_program_balances({"JetBlue TrueBlue": 10000, "American Airlines AAdvantage": 14440}) is True
    # Another machine (fresh process, no local files) sees the update.
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setattr(ledger, "PROGRAM_BALANCES_PATH", str(tmp_path / "elsewhere.json"))
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 10000, "American Airlines AAdvantage": 14440}


def test_gist_failure_falls_back_to_local(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "BALANCES_PATH", str(tmp_path / "balances.json"))
    ledger.save_balances({"bilt": 103000})

    def boom(*a, **k):
        raise ledger.requests.ConnectionError("offline")

    monkeypatch.setattr(ledger.requests, "request", boom)
    monkeypatch.setattr(ledger, "GIST_DISABLED", False)
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setenv("GIST_TOKEN", "t")
    assert ledger.load_balances() == {"bilt": 103000}
    assert ledger.save_balances({"bilt": 90000}) is False
    monkeypatch.setattr(ledger, "GIST_DISABLED", True)
    assert ledger.load_balances() == {"bilt": 90000}
