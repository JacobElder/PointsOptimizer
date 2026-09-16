"""
Smoke check: can this machine get a free Google Flights price via fast-flights?

Makes a few real lookups with SerpApi disabled (so it never spends quota) and
exits non-zero if none succeed. Used by .github/workflows/cash_price_check.yml
to check whether Google allows scraping from GitHub's runners.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import flight_search

ROUTES = [("JFK", "LHR", "ECONOMY"), ("JFK", "CDG", "BUSINESS"), ("EWR", "LIS", "ECONOMY")]


def main() -> int:
    try:
        import fast_flights  # noqa: F401
    except Exception as e:
        import traceback
        tb = traceback.format_exc().strip().splitlines()
        print(f"::error::fast-flights import failed: {type(e).__name__}: {str(e)[:300]} | {' / '.join(tb[-4:])[:600]}")
        return 2
    if not flight_search.fast_flights_available():
        print("::error::fast-flights disabled via POINTSOPT_DISABLE_FAST_FLIGHTS")
        return 2
    flight_search.SERPAPI_MAX_CALLS = 0
    day = (date.today() + timedelta(days=45)).isoformat()
    ok = 0
    for origin, dest, cabin in ROUTES:
        t = time.time()
        try:
            offers = flight_search._fetch_offers_fast_flights(origin, dest, day, cabin)
            msg = f"{origin}-{dest} {cabin} {day}: {len(offers)} offers, cheapest ${offers[0].price_usd:,.0f}" if offers \
                else f"{origin}-{dest} {cabin} {day}: 0 offers"
            ok += bool(offers)
            print(f"::notice::{msg} ({time.time() - t:.1f}s)")
        except Exception as e:
            print(f"::warning::{origin}-{dest} {cabin}: {type(e).__name__}: {str(e)[:200]}")
        time.sleep(2)
    print(f"{ok}/{len(ROUTES)} lookups returned prices")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
