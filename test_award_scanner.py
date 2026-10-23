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


def test_daily_scan_skips_departures_under_30_days_out(monkeypatch):
    """User, Oct 21: never books an international trip a week out; a month or more is
    realistic. Oct 6-21, 42% of emailed cards left within 30 days, including 3 of the 5
    that fell below their bar a day later (close-in fares swing)."""
    class _Capture(_Session):
        def get(self, url, **kw):
            self.params = kw["params"]
            return _Resp()

    monkeypatch.setattr(award_scanner, "transferable_sources", lambda *a, **k: ["aeroplan"])
    today = date(2026, 10, 21)
    s = _Capture({})
    cfg = {**award_scanner.load_config(), "sources": ["aeroplan"], "cabins": ["ECONOMY"]}
    award_scanner.scan(cfg, today=today, session=s)
    assert s.params["start_date"] == "2026-11-20"


# ── Parsing, paging, staleness and the quota check ────────────────────────
from datetime import datetime, timedelta, timezone


def _row(points=30000, date_s="2027-01-10", updated=None, source="aeroplan", taxes=500, **extra):
    updated = updated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ID": f"{source}{date_s}{points}", "Source": source, "Date": date_s, "UpdatedAt": updated,
            "TaxesCurrency": "USD", "YAvailable": True, "YMileageCostRaw": points, "YTotalTaxesRaw": taxes,
            "YRemainingSeatsRaw": 2, "YDirectRaw": True, "YAirlinesRaw": "AC",
            "Route": {"OriginAirport": "JFK", "DestinationAirport": "LIS", "Distance": 3400}, **extra}


class _Pages:
    """Serves a list of (rows, hasMore, cursor) pages in order and records each request's params."""

    def __init__(self, pages):
        self.pages, self.params = list(pages), []

    def get(self, url, **kw):
        self.params.append(kw["params"])
        rows, more, cursor = self.pages.pop(0) if self.pages else ([], False, None)
        resp = _Resp(rows=rows)
        resp.json = lambda: {"data": rows, "hasMore": more, "cursor": cursor}
        return resp


def test_parse_reads_cents_and_rejects_unusable_rows():
    c = award_scanner._parse(_row(points=30000, taxes=21950), "ECONOMY")
    assert (c.points, c.taxes, c.distance, c.program, c.airlines) == (30000, 219.5, 3400, "Air Canada Aeroplan", "AC")
    assert c.as_dict()["taxes"] == 219.5
    assert award_scanner._parse({**_row(), "YAvailable": False}, "ECONOMY") is None
    assert award_scanner._parse({**_row(), "YMileageCostRaw": "n/a"}, "ECONOMY") is None
    assert award_scanner._parse({**_row(), "YMileageCostRaw": 0, "YMileageCost": 0}, "ECONOMY") is None
    # Falls back to the formatted field when the raw one is missing.
    assert award_scanner._parse({**_row(), "YMileageCostRaw": None, "YMileageCost": "12500"}, "ECONOMY").points == 12500
    # An unknown source keeps its slug rather than vanishing.
    assert award_scanner._parse(_row(source="newair"), "ECONOMY").program == "newair"


def test_stale_rows_are_dropped_and_cheapest_duplicate_kept(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [_row(points=40000), _row(points=30000), _row(points=35000),
            _row(points=10000, date_s="2027-01-11", updated=old),  # 11 days old, limit 10
            _row(points=20000, date_s="2027-01-12", updated="not a date"),  # unreadable age: kept
            {**_row(points=5000, date_s="2027-01-13"), "YAvailable": False}]  # no space: skipped
    s = _Pages([(rows, False, None)])
    cands, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert stats["stale"] == 1 and stats["rows"] == 6
    assert sorted((c.date, c.points) for c in cands) == [("2027-01-10", 30000), ("2027-01-12", 20000)]
    assert stats["per_source"] == {"aeroplan": 2} and stats["candidates"] == 2


def test_pages_follow_skip_and_cursor_until_has_more_is_false(monkeypatch):
    s = _Pages([([_row(date_s="2027-01-10")], True, "c1"),
                ([_row(date_s="2027-01-11"), _row(date_s="2027-01-12")], True, "c2"),
                ([_row(date_s="2027-01-13")], False, None)])
    cands, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert [(p["skip"], p.get("cursor")) for p in s.params] == [(0, None), (1, "c1"), (3, "c2")]
    assert len(cands) == 4 and stats["calls"] == 3 and stats["truncated"] == []


def test_hitting_the_page_cap_is_recorded_as_truncated(monkeypatch):
    monkeypatch.setattr(award_scanner, "MAX_PAGES_PER_CABIN", 3)
    s = _Pages([([_row(date_s=f"2027-01-{d:02d}")], True, f"c{d}") for d in range(10, 20)])
    cands, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert len(s.params) == 3 and len(cands) == 3
    assert stats["truncated"] == ["aeroplan/ECONOMY"]


def test_config_dates_override_days_out_and_extra_sources_are_scanned(monkeypatch):
    monkeypatch.setattr(award_scanner, "transferable_sources", lambda *a, **k: {"aeroplan": ["chase_ur"]})
    s = _Pages([])
    cfg = {"origins": ["JFK", "EWR"], "destinations": ["LIS"], "cabins": ["ECONOMY", "BUSINESS"],
           "start_date": "2027-02-01", "end_date": "2027-02-10"}
    _, stats = award_scanner.scan(cfg, today=date(2026, 10, 23), session=s, extra_sources=["delta", "aeroplan"])
    assert stats["sources"] == ["aeroplan", "delta"]
    assert [(p["cabins"], p["sources"]) for p in s.params] == [
        ("economy", "aeroplan"), ("economy", "delta"), ("business", "aeroplan"), ("business", "delta")]
    assert {(p["start_date"], p["end_date"], p["origin_airport"]) for p in s.params} == {
        ("2027-02-01", "2027-02-10", "JFK,EWR")}


def test_one_query_for_all_programs_when_per_source_is_off(monkeypatch):
    monkeypatch.setattr(award_scanner, "transferable_sources", lambda *a, **k: {"aeroplan": [], "united": []})
    s = _Pages([])
    award_scanner.scan({"origins": ["JFK"], "destinations": ["LIS"], "cabins": ["FIRST"]},
                       today=date(2026, 10, 23), session=s, per_source=False)
    assert [(p["cabins"], p["sources"]) for p in s.params] == [("first", "aeroplan,united")]
    assert s.params[0]["start_date"] == "2026-10-26" and s.params[0]["end_date"] == "2027-09-18"  # 3 / 330 days


def test_page_retries_wait_10_then_30_seconds(monkeypatch):
    monkeypatch.setattr(award_scanner, "PAGE_RETRY_WAITS_S", (10, 30))
    waits = []
    monkeypatch.setattr(award_scanner.time, "sleep", waits.append)
    s = _Session({"aeroplan": [_Resp(502), requests.ReadTimeout("slow"), _Resp(rows=[_row()])]})
    cands, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert waits == [10, 30] and len(cands) == 1
    assert stats["calls"] == 3  # a timed-out call may still count against the daily quota


def test_three_failed_attempts_then_the_slice_is_skipped(monkeypatch):
    monkeypatch.setattr(award_scanner, "PAGE_RETRY_WAITS_S", (10, 30))
    monkeypatch.setattr(award_scanner.time, "sleep", lambda s: None)
    s = _Session({"aeroplan": [_Resp(500)]})
    _, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert s.calls == ["aeroplan"] * 3 and stats["failed"] == ["aeroplan/ECONOMY"] and stats["calls"] == 3


def test_a_success_resets_the_consecutive_failure_count(monkeypatch):
    down = [requests.ConnectionError("refused")]
    script = {f"p{i}": down for i in range(6)}
    script["p3"] = [_Resp()]  # one good answer between failures
    sources = [f"p{i}" for i in range(7)]
    s = _Session(script)
    _, stats = _scan(s, sources, monkeypatch)
    assert stats["failed"] == ["p0/ECONOMY", "p1/ECONOMY", "p2/ECONOMY", "p4/ECONOMY", "p5/ECONOMY"]
    assert stats["aborted"] is False and "p6" in s.calls


def test_rate_limit_header_is_recorded(monkeypatch):
    s = _Session({"aeroplan": [_Resp()]})
    _, stats = _scan(s, ["aeroplan"], monkeypatch)
    assert stats["rate_limit_remaining"] == "900"


class _OneResp:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.kw = resp, exc, None

    def get(self, url, **kw):
        self.kw = kw
        if self.exc:
            raise self.exc
        return self.resp


def test_remaining_calls_reads_the_header_with_one_tiny_query():
    s = _OneResp(_Resp())
    assert award_scanner.remaining_calls(s) == 900
    assert s.kw["params"]["take"] == 1 and s.kw["headers"] == {"Partner-Authorization": "k"}


def test_remaining_calls_is_zero_on_429_and_none_when_unknown(monkeypatch):
    assert award_scanner.remaining_calls(_OneResp(_Resp(429))) == 0
    assert award_scanner.remaining_calls(_OneResp(exc=requests.ConnectionError("x"))) is None
    no_header = _Resp()
    no_header.headers = {}
    assert award_scanner.remaining_calls(_OneResp(no_header)) is None
    monkeypatch.setattr(award_scanner.seats_aero, "_get_api_key",
                        lambda: (_ for _ in ()).throw(award_scanner.seats_aero.NotConfigured("no key")))
    assert award_scanner.remaining_calls(_OneResp(_Resp())) is None


def test_sources_for_programs_and_pools_map_names_to_slugs():
    assert award_scanner.sources_for_programs(["United MileagePlus", "Southwest Rapid Rewards",
                                               "JetBlue TrueBlue"]) == ["united", "jetblue"]
    assert "aeroplan" in award_scanner.sources_for_pool("chase_ur")
