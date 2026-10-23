"""Tests for tools/audit_digest.py (`make audit`) and tools/google_probe.py.

The daily audit trusts `make audit` to flag bad cards, so each flag is checked to fire
when it should and stay quiet otherwise. No network, no git history: git reads, Google
lookups and the probe's HTTP client are all faked.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import types
from datetime import date, timedelta

import pytest

import tools.audit_digest as audit
import tools.google_probe as probe

ROOT = os.path.dirname(os.path.abspath(__file__))
RUN = "2026-10-23T13:26:15Z"


def base_card(**kw) -> dict:
    """A real card from the committed digest, made internally consistent and clean."""
    with open(os.path.join(ROOT, "deal_digest.json")) as f:
        x = copy.deepcopy(json.load(f)["top"][0])
    x.update({"origin": "EWR", "dest": "TLV", "program": "Air Canada Aeroplan", "source": "aeroplan",
              "cabin": "BUSINESS", "date": "2027-01-21", "points": 62400, "taxes_usd": 79.81,
              "cash_price": 2488.5, "cpp": 3.86, "great_floor": 2.625, "baseline_cpp": 2.1,
              "surplus_usd": 1098.0, "watch_bar": None, "unverified": False, "rt_unavailable": False,
              "slow": False, "cpp_at_typical": 3.86, "new": False, "reminded": False,
              "trip": {"flights": ["LO16 EWR–WAW", "H3155 WAW–TLV"], "nonstop": False}})
    x.update(kw)
    return x


def digest(top=(), held=(), watch=(), **kw) -> dict:
    d = {"generated_at": RUN, "emailed_new": 1, "email_failed": None,
         "scan": {"candidates": 1234, "calls": 240, "failed": [], "aborted": False},
         "verification": {"trip_lookups": 70, "hit_budget": False}, "round_trip_checks": 600,
         "google_coverage": {"one_way": 240, "one_way_empty": 4, "one_way_recovered": 0,
                             "round_trips": 650, "round_trips_priced": 614},
         "held_miles": list(held), "watchlist": [{"label": "W", "deals": list(watch)}] if watch else [],
         "top": list(top)}
    d.update(kw)
    return d


def card_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln[:5] in ("top  ", "held ", "watch")]


# ── offline(): header and clean cards ─────────────────────────────────────────

def test_clean_digest_has_no_problems_and_prints_the_header(capsys):
    assert audit.offline(digest([base_card()])) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"generated {RUN}  emailed 1  email_failed none"
    assert "scan: 1,234 awards, 240 calls, failed none, aborted False, quota_exhausted False" in out
    assert "verification: 70 flight lookups (normal ~50-80), hit_budget False; round-trip checks 600" in out
    assert "google: one-way 236/240 priced, round trips 614/650 (94%)" in out
    assert card_lines(out) == []  # site-only and clean: not listed
    assert "fares: 0 of 1 published card(s) on an earlier day's fare (expected ~0 from Oct 19)" in out
    assert "usual fare: 0 card(s) under the bar at the route's usual fare, not emailed" in out
    assert "still available" not in out
    assert out.rstrip().endswith("0 card(s) with problems")


def test_emailed_clean_card_is_listed_with_new_but_is_no_problem(capsys):
    assert audit.offline(digest([base_card(new=True)])) == 0
    lines = card_lines(capsys.readouterr().out)
    assert lines == ["top   NEW  EWR-TLV BUSI Air Canada Aeropla 2027-01-21  62,400+$80 $2488 3.86c/bar 2.62 "
                     "| LO16 EWR–WAW · H3155 WAW–TLV "]


def test_digest_without_a_scan_block_prints_unknown_counts(capsys):
    assert audit.offline({"top": []}) == 0
    assert "scan: ? awards, None calls" in capsys.readouterr().out


def test_digest_from_before_the_usual_fare_rule_has_no_usual_fare_line(capsys):
    x = base_card()
    del x["cpp_at_typical"]
    d = digest([x], generated_at=None, emailed_new=None)
    assert audit.offline(d) == 0
    out = capsys.readouterr().out
    assert "generated None  emailed None  email_failed none" in out
    assert "fares: 0 of 1 published card(s)" in out
    assert "usual fare" not in out


# ── offline(): each flag ──────────────────────────────────────────────────────

@pytest.mark.parametrize("change, flag", [
    ({"cpp": 3.90}, "CPP should be 3.86"),
    ({"surplus_usd": 1200.0}, "surplus should be 1098"),
    ({"cpp": 2.0, "cash_price": 1327.81, "surplus_usd": -0.0}, "BELOW BAR"),
    ({"unverified": True}, "UNVERIFIED"),
    ({"rt_unavailable": True}, "RT ESTIMATED"),
    ({"cpp_at_typical": 2.0, "new": True}, "EMAILED UNDER BAR AT USUAL FARE"),
    ({"slow": True, "new": True}, "SLOW EMAILED"),
])
def test_each_flag_fires_and_counts_as_a_problem(capsys, change, flag):
    x = base_card(**change)
    if "cash_price" in change:  # recompute surplus so only BELOW BAR fires
        x["surplus_usd"] = x["cash_price"] - x["taxes_usd"] - x["points"] * x["baseline_cpp"] / 100
    assert audit.offline(digest([x])) == 1
    lines = card_lines(capsys.readouterr().out)
    assert len(lines) == 1 and lines[0].endswith(flag)


def test_cpp_within_a_hundredth_and_surplus_within_a_dollar_pass():
    x = base_card(cpp=3.865, surplus_usd=1098.9)
    assert audit.offline(digest([x])) == 0


def test_below_bar_uses_the_watchlist_bar_when_set(capsys):
    x = base_card(watch_bar=4.0)
    assert audit.offline(digest(watch=[x])) == 1
    line = card_lines(capsys.readouterr().out)[0]
    assert line.startswith("watch      EWR-TLV") and "3.86c/bar 4.00" in line and line.endswith("BELOW BAR")


def test_held_section_cards_are_audited_too(capsys):
    assert audit.offline(digest(held=[base_card(unverified=True)])) == 1
    assert card_lines(capsys.readouterr().out)[0].startswith("held       EWR-TLV")


def test_slow_card_not_emailed_is_not_flagged(capsys):
    assert audit.offline(digest([base_card(slow=True)])) == 0
    assert card_lines(capsys.readouterr().out) == []


def test_slow_emailed_only_counts_from_oct_19(capsys):
    x = base_card(slow=True, new=True)
    assert audit.offline(digest([x], generated_at="2026-10-18T12:36:00Z")) == 0
    line = card_lines(capsys.readouterr().out)[0]
    assert line.endswith(" SLOW ") and "SLOW EMAILED" not in line
    assert audit.offline(digest([x], generated_at="2026-10-19T15:35:00Z")) == 1


def test_under_bar_at_usual_fare_site_only_is_tallied_not_flagged(capsys):
    assert audit.offline(digest([base_card(cpp_at_typical=2.5)])) == 0
    out = capsys.readouterr().out
    assert card_lines(out) == []
    assert "usual fare: 1 card(s) under the bar at the route's usual fare, not emailed: EWR-TLV 2.50c" in out


def test_usual_fare_at_the_bar_exactly_is_fine(capsys):
    assert audit.offline(digest([base_card(cpp_at_typical=2.625, new=True)])) == 0
    assert "usual fare: 0 card(s)" in capsys.readouterr().out


def test_several_flags_on_one_card_count_once(capsys):
    x = base_card(unverified=True, rt_unavailable=True, cpp=3.95, new=True)
    assert audit.offline(digest([x, base_card(dest="LHR", unverified=True)])) == 2
    lines = card_lines(capsys.readouterr().out)
    assert lines[0].endswith("CPP should be 3.86 UNVERIFIED RT ESTIMATED")


# ── offline(): old fares ──────────────────────────────────────────────────────

def quote(x, fetched_at, price=2646.0, **kw):
    return {"origin": x["origin"], "dest": x["dest"], "cabin": x["cabin"], "date": x["date"],
            "price_usd": price, "fetched_at": fetched_at, **kw}


def test_emailed_card_on_an_earlier_days_fare_is_a_problem(capsys):
    x = base_card(new=True)
    assert audit.offline(digest([x]), [quote(x, "2026-10-21T13:00:00Z")]) == 1
    out = capsys.readouterr().out
    assert card_lines(out)[0].endswith("OLD FARE (2026-10-21)")
    assert "fares: 1 of 1 published card(s) on an earlier day's fare" in out


def test_reminded_card_on_an_old_fare_is_a_problem_too(capsys):
    x = base_card(reminded=True)
    assert audit.offline(digest([x]), [quote(x, "2026-10-21T13:00:00Z")]) == 1
    assert card_lines(capsys.readouterr().out)[0].endswith("OLD FARE (2026-10-21)")


def test_site_only_card_on_an_old_fare_is_listed_lowercase_not_a_problem(capsys):
    x = base_card()
    assert audit.offline(digest([x]), [quote(x, "2026-10-21T13:00:00Z")]) == 0
    out = capsys.readouterr().out
    assert card_lines(out)[0].endswith("old fare (2026-10-21)")
    assert "fares: 1 of 1 published" in out and "0 card(s) with problems" in out


def test_site_only_old_fare_plus_a_real_flag_is_a_problem():
    x = base_card(unverified=True)
    assert audit.offline(digest([x]), [quote(x, "2026-10-21T13:00:00Z")]) == 1


def test_fresh_fare_and_no_generated_at_are_not_old(capsys):
    x = base_card(new=True)
    assert audit.offline(digest([x]), [quote(x, "2026-10-23T13:30:00Z")]) == 0
    d = digest([x])
    del d["generated_at"]
    assert audit.offline(d, [quote(x, "2026-10-01T00:00:00Z")]) == 0  # nothing to compare against
    assert "OLD FARE" not in capsys.readouterr().out


# ── old_fare() ────────────────────────────────────────────────────────────────

def test_old_fare_matches_only_the_same_route_cabin_and_date():
    x = base_card()
    old = "2026-10-20T10:00:00Z"
    assert audit.old_fare(x, [quote(x, old)], RUN) == "2026-10-20"
    for other in ({"dest": "LHR"}, {"origin": "JFK"}, {"cabin": "ECONOMY"}, {"date": "2027-01-22"}):
        assert audit.old_fare(x, [{**quote(x, old), **other}], RUN) is None
    assert audit.old_fare(x, [quote(x, old, price=None)], RUN) is None


def test_old_fare_six_hour_boundary():
    x = base_card()
    assert audit.old_fare(x, [quote(x, "2026-10-23T07:26:15Z")], RUN) is None       # exactly 6 h
    assert audit.old_fare(x, [quote(x, "2026-10-23T07:26:14Z")], RUN) == "2026-10-23"  # 6 h 1 s
    assert audit.old_fare(x, [quote(x, "2026-10-24T00:00:00Z")], RUN) is None       # after the run


def test_old_fare_skips_a_priceless_quote_and_finds_the_priced_one():
    x = base_card()
    qs = [quote(x, "2026-10-23T13:30:00Z", price=None), {**quote(x, "2026-10-19T00:00:00Z"), "dest": "TLV"}]
    assert audit.old_fare(x, qs, RUN) == "2026-10-19"


# ── emailed() and AGAIN rows ──────────────────────────────────────────────────

def test_emailed_is_new_or_reminded():
    assert audit.emailed({"new": True}) is True
    assert audit.emailed({"reminded": True}) is True
    assert audit.emailed({"new": False, "reminded": False}) is False
    assert audit.emailed({}) is False


def test_reminded_cards_print_again_and_the_still_available_line(capsys):
    d = digest([base_card(new=True), base_card(dest="LHR", reminded=True), base_card(dest="CDG", reminded=True)])
    assert audit.offline(d) == 0
    out = capsys.readouterr().out
    lines = card_lines(out)
    assert [ln[:20] for ln in lines] == ["top   NEW  EWR-TLV B", "top   AGAINEWR-LHR B", "top   AGAINEWR-CDG B"]
    assert "still available: 2 earlier deal(s) shown again (AGAIN), not counted as repeats" in out


def test_reminded_card_is_held_to_the_email_rules(capsys):
    assert audit.offline(digest([base_card(reminded=True, slow=True)])) == 1
    assert card_lines(capsys.readouterr().out)[0].endswith("SLOW SLOW EMAILED")


# ── google_line() ─────────────────────────────────────────────────────────────

def cov(ow, ow_empty, rt, rt_priced, rec=0):
    return {"google_coverage": {"one_way": ow, "one_way_empty": ow_empty, "one_way_recovered": rec,
                                "round_trips": rt, "round_trips_priced": rt_priced}}


def test_google_line_healthy():
    assert audit.google_line(cov(100, 30, 100, 70)) == "google: one-way 70/100 priced, round trips 70/100 (70%)"


def test_google_line_throttled_on_one_way_over_30_percent_empty():
    assert audit.google_line(cov(100, 31, 100, 100)) == (
        "google: one-way 69/100 priced, round trips 100/100 (100%)  THROTTLED")


def test_google_line_throttled_on_round_trips_under_70_percent():
    assert audit.google_line(cov(100, 0, 100, 69)).endswith("round trips 69/100 (69%)  THROTTLED")


def test_google_line_shows_retries_and_handles_zero_round_trips():
    assert audit.google_line(cov(240, 4, 0, 0, rec=6)) == (
        "google: one-way 236/240 priced (6 on a retry), round trips 0/0 (100%)")


def test_google_line_without_round_trip_counts():
    d = {"google_coverage": {"one_way": 10, "one_way_empty": 5}}
    assert audit.google_line(d) == ("google: one-way 5/10 priced, round trips priced not recorded "
                                    "(see the run log)  THROTTLED")


def test_google_line_for_a_digest_with_no_pricing_at_all():
    assert audit.google_line({}) == "google: one-way 0/0 priced, round trips priced not recorded (see the run log)"


# ── repeats() ─────────────────────────────────────────────────────────────────

class FakeGit:
    """`git log` returns these revs (newest first, as git does); `load` returns their digests."""

    def __init__(self, monkeypatch, history: list[tuple[str, str, dict]]):
        self.history = history
        log = "\n".join(f"{rev} {day}" for rev, day, _ in history) + "\n"
        monkeypatch.setattr(audit.subprocess, "run",
                            lambda *a, **k: types.SimpleNamespace(stdout=log, returncode=0))
        monkeypatch.setattr(audit, "load", lambda rev: {r: d for r, _, d in history}[rev])


def test_repeats_counts_only_new_cards_by_route_key(monkeypatch, capsys):
    tlv = base_card(new=True)
    lhr_old = base_card(dest="LHR", new=True, points=55000)
    FakeGit(monkeypatch, [
        ("c3", "2026-10-23", digest([base_card(new=True, points=50000, date="2027-02-01"),
                                     base_card(dest="LHR", new=False),           # site-only: ignored
                                     base_card(dest="CDG", reminded=True)])),    # reminded: not a repeat
        ("c2", "2026-10-22", digest([base_card(dest="CDG", new=True)], emailed_new=0)),  # no email: skipped
        ("c1", "2026-10-21", digest([tlv, lhr_old, base_card(dest="CDG", new=True, source="united")])),
    ])
    audit.repeats(3)
    assert capsys.readouterr().out.splitlines() == [
        "2026-10-21 c1: 3 emailed, 0 repeat a trip emailed before",
        "2026-10-23 c3: 1 emailed, 1 repeat a trip emailed before: aeroplan|EWR|TLV|BUSINESS",
    ]


def test_repeats_reminded_card_does_not_seed_history(monkeypatch, capsys):
    FakeGit(monkeypatch, [
        ("b2", "2026-10-24", digest([base_card(new=True)])),
        ("b1", "2026-10-23", digest([base_card(reminded=True)])),
    ])
    audit.repeats()
    assert capsys.readouterr().out.splitlines() == [
        "2026-10-23 b1: 0 emailed, 0 repeat a trip emailed before",
        "2026-10-24 b2: 1 emailed, 0 repeat a trip emailed before",
    ]


def test_repeats_lists_at_most_six_keys(monkeypatch, capsys):
    many = [base_card(dest=f"D{i}", new=True) for i in range(8)]
    FakeGit(monkeypatch, [("a2", "2026-10-22", digest(copy.deepcopy(many))), ("a1", "2026-10-21", digest(many))])
    audit.repeats()
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("2026-10-22 a2: 8 emailed, 8 repeat a trip emailed before: aeroplan|EWR|D0|BUSINESS")
    assert last.count("aeroplan|") == 6


# ── live() ────────────────────────────────────────────────────────────────────

def offer(price, stops=0):
    return types.SimpleNamespace(price_usd=price, stops=stops)


class FakeGoogle:
    def __init__(self, monkeypatch, one_way=None, round_trip=None):
        import flight_search
        self.ow_calls, self.rt_calls = [], []
        one_way = one_way if one_way is not None else {}
        round_trip = round_trip if round_trip is not None else {}

        def ow(o, d, dt, cab):
            self.ow_calls.append((o, d, dt, cab))
            r = one_way.get(d, [])
            if isinstance(r, Exception):
                raise r
            return r

        def rt(o, d, dt, ret, cab):
            self.rt_calls.append((o, d, dt, ret, cab))
            r = round_trip.get(d, [])
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(flight_search, "_fetch_offers_fast_flights", ow)
        monkeypatch.setattr(flight_search, "search_round_trip_offers", rt)


def far(days=60):
    return (date.today() + timedelta(days=days)).isoformat()


def test_live_reprices_new_and_reminded_cards_only(monkeypatch, capsys):
    g = FakeGoogle(monkeypatch, one_way={"TLV": [offer(2700.0)], "LHR": [offer(2000.0)]},
                   round_trip={"TLV": [offer(4977.0)], "LHR": [offer(5000.0)]})
    dep = far()
    d = digest([base_card(new=True, date=dep), base_card(dest="LHR", reminded=True, date=dep, rt_stay_nights=4),
                base_card(dest="CDG", date=dep)])                      # site-only: skipped
    audit.live(d)
    assert [c[1] for c in g.ow_calls] == ["TLV", "LHR"]
    ret7 = (date.fromisoformat(dep) + timedelta(days=7)).isoformat()
    ret4 = (date.fromisoformat(dep) + timedelta(days=4)).isoformat()
    assert g.rt_calls == [("EWR", "TLV", dep, ret7, "BUSINESS"), ("EWR", "LHR", dep, ret4, "BUSINESS")]
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "Live re-price of emailed cards (Google Flights; one-way and half a round trip):"
    # TLV: half RT $2,488.50 beats one-way $2,700 -> same as emailed.
    assert out[2] == (f"  EWR-TLV BUSI Air Canada Aer {dep} email 3.86c ($2488) -> live 3.86c ($2488, +0%) bar 2.62")
    # LHR: one-way $2,000 -> (2000 - 79.81) / 62400 = 3.08c.
    assert out[3] == (f"  EWR-LHR BUSI Air Canada Aer {dep} email 3.86c ($2488) -> live 3.08c ($2000, -20%) bar 2.62")


def test_live_skips_departures_tomorrow_or_earlier(monkeypatch, capsys):
    g = FakeGoogle(monkeypatch, one_way={"TLV": [offer(2700.0)]})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    audit.live(digest([base_card(new=True, date=tomorrow), base_card(new=True, date=date.today().isoformat())]))
    assert g.ow_calls == [] and g.rt_calls == []
    audit.live(digest([base_card(new=True, date=far(2))]))
    assert len(g.ow_calls) == 1


def test_live_flags_below_bar_now(monkeypatch, capsys):
    FakeGoogle(monkeypatch, one_way={"TLV": [offer(1500.0)]})
    audit.live(digest([base_card(new=True, date=far())]))
    assert capsys.readouterr().out.rstrip().endswith("live 2.28c ($1500, -40%) bar 2.62  BELOW BAR NOW")


def test_live_nonstop_card_ignores_two_stop_fares(monkeypatch, capsys):
    x = base_card(new=True, date=far(), trip={"flights": [], "nonstop": True})
    FakeGoogle(monkeypatch, one_way={"TLV": [offer(1000.0, stops=2), offer(2600.0, stops=1)]})
    audit.live(digest([x]))
    assert "($2600," in capsys.readouterr().out


def test_live_nonstop_card_falls_back_to_any_fare_when_none_are_close(monkeypatch, capsys):
    x = base_card(new=True, date=far(), trip={"flights": [], "nonstop": True})
    FakeGoogle(monkeypatch, one_way={"TLV": [offer(1900.0, stops=2)]})
    audit.live(digest([x]))
    assert "($1900," in capsys.readouterr().out


def test_live_no_fare_and_errors(monkeypatch, capsys):
    dep = far()
    FakeGoogle(monkeypatch, one_way={"TLV": RuntimeError("throttled"), "LHR": RuntimeError("x")},
               round_trip={"TLV": RuntimeError("throttled"), "LHR": [offer(4000.0)]})
    audit.live(digest([base_card(new=True, date=dep), base_card(dest="LHR", new=True, date=dep)]))
    out = capsys.readouterr().out.splitlines()
    assert out[2] == f"  EWR-TLV BUSI {dep}: no live fare (Google throttling?)"
    assert "live 3.08c ($2000, -20%)" in out[3]  # one-way failed, half of the $4,000 round trip used


# ── main() ────────────────────────────────────────────────────────────────────

def test_main_offline_then_live(monkeypatch):
    calls = []
    monkeypatch.setattr(audit, "load", lambda rev: calls.append(("load", rev)) or {"top": []})
    monkeypatch.setattr(audit, "load_quotes", lambda rev: calls.append(("quotes", rev)) or [])
    monkeypatch.setattr(audit, "offline", lambda d, q: calls.append(("offline", q)))
    monkeypatch.setattr(audit, "live", lambda d: calls.append(("live",)))
    monkeypatch.setattr(audit, "repeats", lambda: calls.append(("repeats",)))
    monkeypatch.setattr(sys, "argv", ["audit_digest.py", "abc123", "--live"])
    audit.main()
    assert calls == [("load", "abc123"), ("quotes", "abc123"), ("offline", []), ("live",)]
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["audit_digest.py"])
    audit.main()
    assert calls == [("load", "HEAD"), ("quotes", "HEAD"), ("offline", [])]
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["audit_digest.py", "--repeats"])
    audit.main()
    assert calls == [("repeats",)]


def test_load_and_load_quotes_read_git_show(monkeypatch):
    seen = []

    def run(cmd, **k):
        seen.append(cmd)
        if cmd[2].endswith("cash_quotes.json"):
            return types.SimpleNamespace(stdout='{"quotes": [{"a": 1}]}', returncode=0)
        return types.SimpleNamespace(stdout='{"top": []}', returncode=0)
    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.load("r1") == {"top": []}
    assert audit.load_quotes("r1") == [{"a": 1}]
    assert seen == [["git", "show", "r1:deal_digest.json"], ["git", "show", "r1:cash_quotes.json"]]
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="", returncode=128))
    assert audit.load_quotes("old") == []  # revisions before cash_quotes.json was committed


# ── google_probe ──────────────────────────────────────────────────────────────

def test_probe_classify_priced_empty_and_parse_error(monkeypatch):
    import flight_search
    monkeypatch.setattr(flight_search, "_parse_google_flights_html", lambda html, cab: [object()])
    assert probe.classify(200, "x", "ECONOMY") == "priced"
    monkeypatch.setattr(flight_search, "_parse_google_flights_html", lambda html, cab: [])
    assert probe.classify(200, "x", "ECONOMY") == "google: no flights"

    def boom(html, cab):
        raise ValueError("bad")
    monkeypatch.setattr(flight_search, "_parse_google_flights_html", boom)
    assert probe.classify(200, "x", "ECONOMY") == "parse error ValueError"


@pytest.mark.parametrize("marker", ["https://www.google.com/sorry/index", "consent.google.com"])
def test_probe_classify_other_block_markers(marker):
    assert probe.classify(200, f"<a href='{marker}'>", "ECONOMY") == "block page"


def write_quotes(tmp_path, quotes):
    (tmp_path / "cash_quotes.json").write_text(json.dumps({"quotes": quotes}))


def test_latest_empties_takes_the_newest_runs_future_empties(monkeypatch, tmp_path):
    soon, far_d = far(2), far(30)
    past = far(1)
    q = lambda d, fetched, price=None, **k: {"origin": "EWR", "dest": "X", "cabin": "ECONOMY",
                                            "date": d, "price_usd": price, "fetched_at": fetched, **k}
    write_quotes(tmp_path, [
        q(far_d, "2026-10-22T13:00:00Z"),                  # older run
        q(far_d, "2026-10-23T13:00:00Z", dest="A"),
        q(soon, "2026-10-23T13:01:00Z", dest="B"),         # 2 days out: still in
        q(past, "2026-10-23T13:02:00Z", dest="C"),         # tomorrow: too soon
        q(far_d, "2026-10-23T13:03:00Z", price=900.0),     # priced: not an empty
        q(far_d, "", dest="D"),                            # no fetched_at
        q(far_d, "2026-10-23T13:04:00Z", dest="E"),
    ])
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    assert [x["dest"] for x in probe.latest_empties(30)] == ["A", "B", "E"]
    assert [x["dest"] for x in probe.latest_empties(2)] == ["A", "B"]


def test_latest_empties_none(monkeypatch, tmp_path):
    write_quotes(tmp_path, [{"origin": "EWR", "dest": "X", "cabin": "ECONOMY", "date": far(),
                             "price_usd": 1.0, "fetched_at": "2026-10-23T00:00:00Z"}])
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    assert probe.latest_empties(5) == []


def test_probe_main_with_a_fake_client(monkeypatch, tmp_path, capsys):
    """The whole probe loop with primp faked: tallies, saved pages, rotation, summary."""
    import flight_search
    write_quotes(tmp_path, [{"origin": "EWR", "dest": d, "cabin": "BUSINESS", "date": far(),
                             "price_usd": None, "fetched_at": "2026-10-23T13:00:00Z"} for d in ("A", "B", "C")])
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "OUT", str(tmp_path / "probe_out"))
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    monkeypatch.setattr(flight_search, "_parse_google_flights_html",
                        lambda html, cab: [object()] if "PRICED" in html else [])

    class Client:
        n = 0

        def __init__(self, **kw):
            self.kw = kw

        def get(self, url, params=None):
            Client.n += 1
            if params and params.get("gl") == "us":
                raise ConnectionError("reset")
            text = "PRICED" if params and "tfs" in params and Client.n % 2 else "empty"
            return types.SimpleNamespace(status_code=200, text=text, cookies={"NID": "1"})
    monkeypatch.setitem(sys.modules, "primp", types.SimpleNamespace(Client=Client))
    monkeypatch.setattr(sys, "argv", ["google_probe.py", "--n", "3", "--pause", "0"])
    probe.main()
    out = capsys.readouterr().out
    assert "warm-up: http 200, 5 bytes, 1 cookie(s)" in out
    assert "3 lookups that came back empty in the latest run, x3 variants" in out
    assert "session_gl=error ConnectionError" in out
    assert "  session_gl  priced 0/3 (0%)  error ConnectionError 3" in out
    saved = sorted(os.listdir(tmp_path / "probe_out"))
    assert "session_gl_error_ConnectionError_1.html" in saved and "session_gl_error_ConnectionError_2.html" in saved
    assert "session_gl_error_ConnectionError_3.html" not in saved  # two pages per kind at most
    fresh = [ln for ln in out.splitlines() if ln.startswith("  fresh")][0]
    session = [ln for ln in out.splitlines() if ln.startswith("  session    ")][0]
    # Every non-error answer is either priced or "no flights"; 3 each for fresh and session.
    for ln in (fresh, session):
        assert "/3 (" in ln


def test_emailed_card_under_bar_at_usual_fare_is_a_problem_not_in_the_not_emailed_list(capsys):
    """The `usual fare:` line lists cards the rule kept out of the email; an emailed card
    that broke the rule is a problem, and listing it there as 'not emailed' was wrong."""
    assert audit.offline(digest([base_card(cpp_at_typical=2.5, new=True)])) == 1
    out = capsys.readouterr().out
    assert "EMAILED UNDER BAR AT USUAL FARE" in out
    assert "usual fare: 0 card(s) under the bar at the route's usual fare, not emailed" in out
