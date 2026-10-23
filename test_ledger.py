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
        self.updated_at = "2026-09-23T00:00:00Z"

    def bump(self):
        self.updated_at = "2026-09-23T01:00:00Z"

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
            data = {"updated_at": self.updated_at,
                    "files": {n: {"content": c} for n, c in self.gists[gid].items()}}

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
    monkeypatch.setattr(ledger, "_gist_unavailable", False)
    # A failed read still shows the local copy...
    assert ledger.load_balances() == {"bilt": 103000}
    # ...but must NOT then write: on Streamlit Cloud the local copy is empty, and
    # saving what the page rendered would wipe the real balances in the Gist.
    assert ledger.save_balances({}) is False
    monkeypatch.setattr(ledger, "GIST_DISABLED", True)
    assert ledger.load_balances() == {"bilt": 103000}


def test_gist_write_is_refused_when_someone_else_changed_it(monkeypatch):
    fake = _FakeGitHub()
    monkeypatch.setattr(ledger.requests, "request", fake)
    monkeypatch.setattr(ledger, "GIST_DISABLED", False)
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setattr(ledger, "_gist_unavailable", False)
    monkeypatch.setenv("GIST_TOKEN", "t")
    # Local balances to seed from: without them no gist is created, the read "fails",
    # and the save below is refused by gist_failed() instead -- the test passed that
    # way without ever reaching the concurrent-change check (found Oct 23).
    with open(ledger.BALANCES_PATH, "w") as f:
        json.dump({"chase_ur": 137000}, f)
    ledger.load_balances()  # seeds the gist and records its updated_at
    assert ledger.gist_failed() is False and len(fake.gists) == 1
    fake.bump()  # another writer (the site, or the daily run) saves
    assert ledger.save_balances({"chase_ur": 1}) is False
    (content,) = fake.gists["g1"].values()
    assert json.loads(content)["cards"] == {"chase_ur": 137000}  # their copy survives


def test_missing_gist_is_not_created_from_nothing(monkeypatch):
    """CI has no local balance files. Creating a gist there published an empty one
    that GitHub then listed first, hiding the real balances from the app."""
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setattr(ledger, "_find_gist_id", lambda: None)
    monkeypatch.setattr(ledger, "_load_local_balances", dict)
    monkeypatch.setattr(ledger, "_load_local_program_balances", dict)
    wrote = []
    monkeypatch.setattr(ledger, "_gist_write", lambda data: wrote.append(data))

    assert ledger._gist_read() is None
    assert wrote == []
    assert ledger.gist_failed() is True  # so saves refuse rather than overwrite


def test_tests_never_touch_the_real_balance_files():
    """conftest redirects both files for every test; a test that forgot to wiped the
    user's real balances.json on each run (Sep 26 - Oct 5)."""
    real_dir = os.path.dirname(os.path.abspath(ledger.__file__))
    assert os.path.dirname(ledger.BALANCES_PATH) != real_dir
    assert os.path.dirname(ledger.PROGRAM_BALANCES_PATH) != real_dir


# ── Gist edge cases ───────────────────────────────────────────────────────
def _gist_on(monkeypatch, fake):
    monkeypatch.setattr(ledger.requests, "request", fake)
    monkeypatch.setattr(ledger, "GIST_DISABLED", False)
    monkeypatch.setenv("GIST_TOKEN", "t")


def test_token_comes_from_streamlit_secrets_when_not_in_the_env(monkeypatch):
    import sys
    import types
    monkeypatch.setattr(ledger, "GIST_DISABLED", False)
    monkeypatch.delenv("GIST_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={"GIST_TOKEN": "s"}))
    assert ledger._gist_token() == "s" and ledger.gist_enabled() is True
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={"GIST_TOKEN": ""}))
    assert ledger._gist_token() is None and ledger.gist_enabled() is False
    monkeypatch.setitem(sys.modules, "streamlit", None)  # import fails
    assert ledger._gist_token() is None


def test_a_failed_first_read_during_save_writes_nothing_to_the_gist(monkeypatch):
    """The save is the first Gist access in this process and the read fails: the
    page's values must not be pushed over the real balances."""
    calls = []

    def flaky(method, url, **kw):
        calls.append(method)
        raise ledger.requests.ConnectionError("offline")

    _gist_on(monkeypatch, flaky)
    assert ledger.save_program_balances({"JetBlue TrueBlue": 1}) is False
    assert calls == ["GET"]  # only the failed read; no PATCH or POST
    assert ledger.gist_failed() is True
    # From now on, saves are refused outright, locally too.
    assert ledger.save_program_balances({"JetBlue TrueBlue": 2}) is False
    assert ledger._load_local_program_balances() == {"JetBlue TrueBlue": 1}


def test_gist_write_failure_reports_not_synced(monkeypatch):
    fake = _FakeGitHub()
    ledger.save_balances({"bilt": 103000})
    _gist_on(monkeypatch, fake)
    ledger.load_balances()  # seeds

    def no_patch(method, url, **kw):
        if method == "PATCH":
            raise ledger.requests.HTTPError("403")
        return fake(method, url, **kw)

    monkeypatch.setattr(ledger.requests, "request", no_patch)
    assert ledger.save_balances({"bilt": 90000}) is False
    assert ledger._load_local_balances() == {"bilt": 90000}  # the local file still saved


def test_seeded_gist_that_cannot_be_found_again_returns_the_seed(monkeypatch):
    ledger.save_balances({"chase_ur": 5})
    monkeypatch.setattr(ledger, "_find_gist_id", lambda: None)
    wrote = []
    monkeypatch.setattr(ledger, "_gist_write", wrote.append)
    data = ledger._gist_read()
    assert data["cards"] == {"chase_ur": 5} and wrote[0]["cards"] == {"chase_ur": 5}


def test_gist_values_are_coerced_and_zero_programs_dropped(monkeypatch):
    fake = _FakeGitHub()
    fake.gists["g1"] = {ledger.GIST_FILENAME: json.dumps(
        {"cards": {"chase_ur": "139915", "bilt": "lots", "wf": 0.0},
         "programs": {"JetBlue TrueBlue": 22516.7, "Delta SkyMiles": 0, "X": None}})}
    _gist_on(monkeypatch, fake)
    assert ledger.load_balances() == {"chase_ur": 139915, "wf": 0}
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 22516}


def test_gist_lookup_pages_until_a_short_page(monkeypatch):
    pages = {1: [{"id": f"x{i}", "files": {"other.json": {}}} for i in range(100)],
             2: [{"id": "real", "files": {ledger.GIST_FILENAME: {}}}]}
    asked = []

    def gh(method, path, params=None, **kw):
        asked.append(params["page"])
        return pages.get(params["page"], [])

    monkeypatch.setattr(ledger, "_gh", gh)
    assert ledger._find_gist_id() == "real" and asked == [1, 2]
    assert ledger._find_gist_id() == "real" and asked == [1, 2]  # remembered


def test_local_program_balances_skip_bad_values(monkeypatch):
    monkeypatch.delenv("PROGRAM_BALANCES", raising=False)
    with open(ledger.PROGRAM_BALANCES_PATH, "w") as f:
        json.dump({"JetBlue TrueBlue": "22516", "United MileagePlus": "n/a", "Delta SkyMiles": -3}, f)
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 22516}
    monkeypatch.setenv("PROGRAM_BALANCES", "[1, 2]")  # valid JSON, not an object: ignored
    assert ledger.load_program_balances() == {"JetBlue TrueBlue": 22516}


def test_balances_temp_file_cleanup_survives_a_missing_temp(paths, monkeypatch):
    def boom(src, dst):
        os.unlink(src)  # the temp file is already gone when cleanup runs
        raise RuntimeError("crash")

    monkeypatch.setattr(ledger.os, "replace", boom)
    with pytest.raises(RuntimeError, match="crash"):
        ledger.save_balances({"amex_mr": 1})


def test_balances_never_reach_a_tracked_file():
    """Public repo: both balance files must be gitignored."""
    import subprocess
    repo = os.path.dirname(os.path.abspath(ledger.__file__))
    for name in ("balances.json", "program_balances.json"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=repo)
        assert r.returncode == 0, f"{name} is not gitignored"


def test_failed_program_balance_save_keeps_the_old_file(monkeypatch):
    """save_balances already writes atomically; program balances were written in place,
    so a crash mid-write could leave program_balances.json truncated."""
    import json as _json
    monkeypatch.delenv("GIST_TOKEN", raising=False)
    ledger.save_program_balances({"United MileagePlus": 5980})
    from pathlib import Path
    before = Path(ledger.PROGRAM_BALANCES_PATH).read_text()

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ledger.json, "dump", _boom)
    try:
        ledger.save_program_balances({"United MileagePlus": 9999})
    except OSError:
        pass
    monkeypatch.setattr(ledger.json, "dump", _json.dump)
    assert Path(ledger.PROGRAM_BALANCES_PATH).read_text() == before
