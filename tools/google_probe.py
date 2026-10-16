"""What Google Flights sends GitHub's runners, and whether a different request gets fares.

Since Oct 10 2026 about half the Deal Finder's Google lookups come back empty on the
runner, while the same lookups from a home connection price (Oct 15: 37 of 40).
This re-runs the latest run's empty one-way lookups three ways, interleaved so the
time of day affects each the same:

    fresh       what deal_finder does: a new impersonated-Chrome client per lookup
    session     one client kept for the whole probe, warmed on the Flights home page
    session_gl  the same, plus gl=us (region) on every lookup

Each answer is classified (priced / Google's "no flights" / no results payload / a
block or consent page / error) and the first pages of each kind are saved to
probe_out/ for reading. Google only: no seats.aero calls, no email, no repo writes.

    .venv/bin/python tools/google_probe.py [--n 30]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_out")
URL = "https://www.google.com/travel/flights"
# Only looked for on pages without results: normal result pages mention "captcha" in scripts.
BLOCK_MARKERS = ("unusual traffic", "/sorry/", "consent.google")


def classify(status: int, html: str, cabin: str) -> str:
    """One word for what Google sent back."""
    import flight_search
    if status != 200:
        return f"http {status}"
    try:
        offers = flight_search._parse_google_flights_html(html, cabin)
    except flight_search.SearchFailed:
        return "block page" if any(m in html.lower() for m in BLOCK_MARKERS) else "no payload"
    except Exception as e:
        return f"parse error {type(e).__name__}"
    return "priced" if offers else "google: no flights"


def latest_empties(n: int) -> list[dict]:
    """The newest run's one-way lookups that came back empty, still in the future."""
    with open(os.path.join(ROOT, "cash_quotes.json")) as f:
        quotes = json.load(f)["quotes"]
    empty = [q for q in quotes if q.get("price_usd") is None and q.get("fetched_at")]
    if not empty:
        return []
    day = max(q["fetched_at"][:10] for q in empty)
    soon = (date.today() + timedelta(days=2)).isoformat()
    return [q for q in empty if q["fetched_at"].startswith(day) and q["date"] >= soon][:n]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=30, help="lookups per variant")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between lookups")
    a = ap.parse_args()

    import flight_search
    from fast_flights import FlightQuery, create_query
    from primp import Client

    def new_client():
        return Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)

    session = new_client()
    warm = session.get(URL, params={"hl": "en"})
    print(f"warm-up: http {warm.status_code}, {len(warm.text):,} bytes, "
          f"{len(warm.cookies or {})} cookie(s)")

    variants = {
        "fresh": lambda p: new_client().get(URL, params=p),
        "session": lambda p: session.get(URL, params=p),
        "session_gl": lambda p: session.get(URL, params={**p, "gl": "us"}),
    }
    os.makedirs(OUT, exist_ok=True)
    tally: dict[str, collections.Counter] = {v: collections.Counter() for v in variants}
    saved: collections.Counter = collections.Counter()
    rows = latest_empties(a.n)
    print(f"{len(rows)} lookups that came back empty in the latest run, x{len(variants)} variants")
    for i, q in enumerate(rows):
        params = create_query(
            flights=[FlightQuery(date=q["date"], from_airport=q["origin"], to_airport=q["dest"])],
            seat=flight_search._FF_SEAT.get(q["cabin"], "economy"), trip="one-way",
            currency="USD", language="en").params()
        order = list(variants)[i % 3:] + list(variants)[:i % 3]  # rotate who goes first
        cells = []
        for name in order:
            try:
                r = variants[name](params)
                kind, html, status = classify(r.status_code, r.text, q["cabin"]), r.text, r.status_code
            except Exception as e:
                kind, html, status = f"error {type(e).__name__}", str(e), 0
            tally[name][kind] += 1
            if kind != "priced" and saved[(name, kind)] < 2:
                saved[(name, kind)] += 1
                fn = f"{name}_{kind.replace(' ', '_').replace(':', '')}_{saved[(name, kind)]}.html"
                with open(os.path.join(OUT, fn), "w") as f:
                    f.write(f"<!-- http {status} {q['origin']}-{q['dest']} {q['cabin']} {q['date']} -->\n{html}")
            cells.append(f"{name}={kind}")
            time.sleep(a.pause)
        print(f"{q['origin']}-{q['dest']} {q['cabin'][:4]} {q['date']}: " + "  ".join(cells))
    print("\nSummary (per variant):")
    for name, c in tally.items():
        total = sum(c.values()) or 1
        print(f"  {name:11} priced {c['priced']}/{total} ({c['priced'] / total:.0%})  " +
              ", ".join(f"{k} {v}" for k, v in c.most_common() if k != "priced"))


if __name__ == "__main__":
    main()
