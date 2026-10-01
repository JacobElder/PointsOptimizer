"""The scan survives a slow or flaky seats.aero instead of losing the day's run."""
from datetime import date

import pytest
import requests

import award_scanner


class _Resp:
    def __init__(self, status=200, rows=None):
        self.status_code = status
        self.headers = {"x-ratelimit-remaining": "900"}
        self._rows = rows or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return {"data": self._rows, "hasMore": False}


class _Session:
    """Answers per program (`sources` param) from a script: an exception is raised,
    a response is returned; the last entry repeats."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def get(self, url, **kw):
        src = kw["params"]["sources"]
        self.calls.append(src)
        steps = self.script.get(src, [_Resp()])
        step = steps[min(sum(1 for c in self.calls if c == src) - 1, len(steps) - 1)]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(award_scanner.seats_aero, "_get_api_key", lambda: "k")
    monkeypatch.setattr(award_scanner, "PAGE_RETRY_WAITS_S", (0, 0))


def _cfg(sources):
    return {"origins": ["JFK"], "destinations": ["LIS"], "cabins": ["ECONOMY"], "sources": sources}


def _scan(session, sources, monkeypatch):
    monkeypatch.setattr(award_scanner, "transferable_sources", lambda *a, **k: sources)
    return award_scanner.scan(_cfg(sources), today=date.today(), session=session)


def test_a_timeout_is_retried(monkeypatch):
    """Oct 1: one 90-second timeout, uncaught, killed the whole run."""
    s = _Session({"aeroplan": [requests.ReadTimeout("slow"), _Resp()]})
    cands, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert stats["failed"] == [] and s.calls == ["aeroplan", "aeroplan"]


def test_a_program_that_keeps_failing_is_skipped_not_fatal(monkeypatch):
    s = _Session({"aeroplan": [requests.ReadTimeout("slow")], "united": [_Resp(503)]})
    cands, stats = _scan(s, ["aeroplan", "united", "alaska"], monkeypatch)
    assert stats["failed"] == ["aeroplan/ECONOMY", "united/ECONOMY"] and not stats["aborted"]
    assert s.calls.count("alaska") == 1  # the rest of the scan still ran


def test_seats_aero_down_stops_the_scan_early(monkeypatch):
    down = [requests.ConnectionError("refused")]
    sources = [f"p{i}" for i in range(8)]
    s = _Session({p: down for p in sources})
    cands, stats = _scan(s, sources, monkeypatch)
    assert stats["aborted"] and len(stats["failed"]) == award_scanner.MAX_CONSECUTIVE_FAILURES
    assert "p7" not in s.calls


def test_a_bad_key_still_fails_loudly(monkeypatch):
    s = _Session({"aeroplan": [_Resp(401)]})
    with pytest.raises(requests.HTTPError):
        _scan(s, ["aeroplan"], monkeypatch)
