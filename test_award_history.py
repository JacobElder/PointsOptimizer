"""award_history: pruning must not erase a route whose price never changed."""

import types
from datetime import date, datetime, timedelta, timezone

import award_history


def _at(monkeypatch, day: date):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    monkeypatch.setattr(award_history, "datetime", FakeDT)


def _cand(points=80000, travel="2027-12-01"):
    return types.SimpleNamespace(source="united", origin="EWR", dest="NRT", cabin="BUSINESS",
                                 date=travel, points=points)


def test_an_unchanged_price_survives_the_prune(monkeypatch, tmp_path):
    path = str(tmp_path / "h.json")
    key = award_history.key_for("united", "EWR", "NRT", "BUSINESS", "2027-12-01")
    start = date(2027, 1, 1)
    for i in range(200):  # same 80k every day for 200 days, KEEP_DAYS is 180
        _at(monkeypatch, start + timedelta(days=i))
        hist = award_history.record([_cand()], path)
    ser = award_history.series(hist)
    assert len(ser.get(key, [])) == 91, "the route's 90-day window should still be full"
    assert award_history.percentile(ser, key, 80000)[0] == 0.5


def test_the_baseline_keeps_the_last_price_before_the_cutoff(monkeypatch, tmp_path):
    path = str(tmp_path / "h.json")
    key = award_history.key_for("united", "EWR", "NRT", "BUSINESS", "2027-12-01")
    start = date(2027, 1, 1)
    for i in range(200):
        _at(monkeypatch, start + timedelta(days=i))
        hist = award_history.record([_cand(90000 if i < 5 else 70000)], path)
    days = sorted(hist)
    assert days[0] == (start + timedelta(days=199 - 180)).isoformat()
    assert hist[days[0]][key] == 70000
    assert sum(1 for d in days if key in hist[d]) == 1  # unchanged after that: stored once


def test_past_travel_months_leave_the_baseline(monkeypatch, tmp_path):
    path = str(tmp_path / "h.json")
    gone = award_history.key_for("united", "EWR", "NRT", "BUSINESS", "2027-02-01")
    start = date(2027, 1, 1)
    for i in range(200):
        _at(monkeypatch, start + timedelta(days=i))
        hist = award_history.record([_cand(travel="2027-02-01")] if i == 0 else [_cand()], path)
    assert all(gone not in v for v in hist.values())
