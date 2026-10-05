"""Audit one Deal Finder digest: the daily "check the digest" routine (see CLAUDE.md).

    .venv/bin/python tools/audit_digest.py            # today's digest (HEAD), offline checks
    .venv/bin/python tools/audit_digest.py --live     # + live Google Flights re-price (free, ~2 min)
    .venv/bin/python tools/audit_digest.py 4ceef5c    # a past digest, by git revision
    .venv/bin/python tools/audit_digest.py --repeats  # repeat-email history over the last 10 digests

Offline checks need no network: card math (CPP and "$ better" recomputed from the
card's own numbers), every card clears its bar, nothing unverified or valued on the
estimated round trip, scan failures, verification cost. --live re-prices each NEW card
(what was emailed) against today's one-way and round-trip fares, using the same
"cheapest of one-way and half a round trip" rule as the run. It never calls seats.aero,
so it doesn't touch the 1,000/day quota.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(rev: str) -> dict:
    out = subprocess.run(["git", "show", f"{rev}:deal_digest.json"], capture_output=True, text=True,
                         cwd=ROOT, check=True).stdout
    return json.loads(out)


def cards(d: dict) -> list[tuple[str, dict]]:
    return ([("held", x) for x in d.get("held_miles", [])]
            + [("watch", y) for g in d.get("watchlist", []) for y in g.get("deals", [])]
            + [("top", x) for x in d.get("top", [])])


def bar_of(x: dict) -> float:
    return x.get("watch_bar") or x["great_floor"]


def offline(d: dict) -> int:
    problems = 0
    v, scan = d.get("verification", {}), d.get("scan", {})
    print(f"generated {d.get('generated_at')}  emailed {d.get('emailed_new')}  "
          f"email_failed {d.get('email_failed') or 'none'}")
    print(f"scan: {scan.get('candidates', '?'):,} awards, {scan.get('calls')} calls, "
          f"failed {scan.get('failed') or 'none'}, aborted {scan.get('aborted', False)}, "
          f"quota_exhausted {scan.get('quota_exhausted', False)}")
    print(f"verification: {v.get('trip_lookups')} flight lookups (normal ~50-80), "
          f"hit_budget {v.get('hit_budget')}; round-trip checks {d.get('round_trip_checks')}")
    for sec, x in cards(d):
        flags = []
        cpp = (x["cash_price"] - x["taxes_usd"]) / x["points"] * 100
        surplus = x["cash_price"] - x["taxes_usd"] - x["points"] * x["baseline_cpp"] / 100
        if abs(cpp - x["cpp"]) > 0.01:
            flags.append(f"CPP should be {cpp:.2f}")
        if abs(surplus - x["surplus_usd"]) > 1:
            flags.append(f"surplus should be {surplus:.0f}")
        if x["cpp"] < bar_of(x):
            flags.append("BELOW BAR")
        if x.get("unverified"):
            flags.append("UNVERIFIED")
        if x.get("rt_unavailable"):
            flags.append("RT ESTIMATED")
        problems += bool(flags)
        if x.get("new") or flags:
            t = x.get("trip") or {}
            print(f"{sec:5} {'NEW ' if x.get('new') else '    '}{x['origin']}-{x['dest']} {x['cabin'][:4]} "
                  f"{x['program'][:18]:18} {x['date']} {x['points']:>7,}+${x['taxes_usd']:.0f} "
                  f"${x['cash_price']:.0f} {x['cpp']:.2f}c/bar {bar_of(x):.2f} "
                  f"| {' · '.join(t.get('flights') or ['no flights'])}"
                  f"{' SLOW' if x.get('slow') else ''} {' '.join(flags)}")
    print(f"{problems} card(s) with problems")
    return problems


def live(d: dict) -> None:
    import flight_search as f
    from deal_finder import price_cabin
    cutoff = (date.today() + timedelta(days=1)).isoformat()
    print("\nLive re-price of emailed cards (Google Flights; one-way and half a round trip):")
    for sec, x in cards(d):
        if not x.get("new") or x["date"] <= cutoff:
            continue
        cab, dep = price_cabin(x["cabin"]), date.fromisoformat(x["date"])
        nonstop = (x.get("trip") or {}).get("nonstop")

        def cheapest(offers):
            pool = [o for o in offers if o.stops <= 1] if nonstop else offers
            return min((o.price_usd for o in (pool or offers)), default=None)
        try:
            ow = cheapest(f._fetch_offers_fast_flights(x["origin"], x["dest"], x["date"], cab))
        except Exception:
            ow = None
        try:
            ret = (dep + timedelta(days=x.get("rt_stay_nights") or 7)).isoformat()
            rt = cheapest(f.search_round_trip_offers(x["origin"], x["dest"], x["date"], ret, cab))
            half = rt / 2 if rt else None
        except Exception:
            half = None
        fares = [p for p in (ow, half) if p]
        if not fares:
            print(f"  {x['origin']}-{x['dest']} {x['cabin'][:4]} {x['date']}: no live fare (Google throttling?)")
            continue
        now = min(fares)
        cpp = (now - x["taxes_usd"]) / x["points"] * 100
        print(f"  {x['origin']}-{x['dest']} {x['cabin'][:4]} {x['program'][:14]:14} {x['date']} "
              f"email {x['cpp']:.2f}c (${x['cash_price']:.0f}) -> live {cpp:.2f}c (${now:.0f}, "
              f"{(now / x['cash_price'] - 1) * 100:+.0f}%) bar {bar_of(x):.2f}"
              f"{'  BELOW BAR NOW' if cpp < bar_of(x) else ''}")


def repeats(n: int = 10) -> None:
    import deal_finder as df
    revs = subprocess.run(["git", "log", f"-{n}", "--format=%h %ad", "--date=short", "--", "deal_digest.json"],
                          capture_output=True, text=True, cwd=ROOT).stdout.split("\n")
    seen: dict[str, str] = {}
    for rev, day in [r.split() for r in revs if r][::-1]:
        d = load(rev)
        if not d.get("emailed_new"):
            continue
        new = [x for _, x in cards(d) if x.get("new")]
        rep = [df.route_report_key(x) for x in new if df.route_report_key(x) in seen]
        for x in new:
            seen.setdefault(df.route_report_key(x), day)
        print(f"{day} {rev}: {len(new)} emailed, {len(rep)} repeat a trip emailed before"
              + (f": {', '.join(rep[:6])}" if rep else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rev", nargs="?", default="HEAD")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--repeats", action="store_true")
    a = ap.parse_args()
    if a.repeats:
        repeats()
        return
    d = load(a.rev)
    offline(d)
    if a.live:
        live(d)


if __name__ == "__main__":
    main()
