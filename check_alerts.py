"""
Batch-evaluate seats.aero alert emails against live cash prices.

seats.aero alerts tell you a route clears your points/fee thresholds, but not
whether it's actually good value in cents-per-point terms — that still needs a
live cash price for the same route/date/cabin. This script closes that gap:
feed it a JSON list of parsed alerts (see seats_aero_alerts.parse_alert_email
for how to produce one from raw alert email HTML) and it looks up each route's
live cash price via flight_search, computes CPP, and ranks the results.

There's no way to fetch the alert emails themselves from here — seats.aero's
API has no "list my alerts" endpoint, and this script has no Gmail credentials.
Fetching + parsing the emails happens in a Claude Code session with Gmail
access; this script is the second half of that pipeline.

Usage:
    python3 check_alerts.py alerts.json

alerts.json is a list of objects with: origin, dest, program, cabin, date,
points, taxes, currency (default "USD").
"""

from __future__ import annotations

import json
import sys

import requests

import deal_log
import flight_search

# Used only if the live rate lookup below fails (offline, API down, etc).
_FX_FALLBACK = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27}

# Kept for backward-compatible imports; the authoritative verdict thresholds now
# live in deal_log (cabin-aware) and drive verdict_for(), so BOOK == "great".
SKIP_FLOOR = deal_log.SKIP_CPP
BOOK_FLOOR = 1.7

_fx_cache: dict[str, float] = {}


def _fx_rate(currency: str) -> float:
    """USD-per-1-unit-of-currency, from a free no-key ECB-backed API, cached per process."""
    if currency == "USD":
        return 1.0
    if currency in _fx_cache:
        return _fx_cache[currency]
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": currency, "to": "USD"},
            timeout=5,
        )
        resp.raise_for_status()
        rate = resp.json()["rates"]["USD"]
    except (requests.RequestException, KeyError, ValueError):
        rate = _FX_FALLBACK.get(currency, 1.0)
    _fx_cache[currency] = rate
    return rate


def evaluate_alerts(alerts: list[dict], skip_floor: float = SKIP_FLOOR, book_floor: float = BOOK_FLOOR) -> list[dict]:
    results = []
    price_cache: dict[tuple, tuple] = {}  # dedupes repeat origin/dest/date/cabin within one batch
    for a in alerts:
        taxes_usd = float(a["taxes"]) * _fx_rate(a.get("currency", "USD"))

        price_key = (a["origin"], a["dest"], a["date"], a["cabin"])
        if price_key in price_cache:
            cash_price, error = price_cache[price_key]
        else:
            cash_price, error = None, None
            try:
                offers = flight_search.search_cash_price(a["origin"], a["dest"], a["date"], a["cabin"], max_results=1)
                cash_price = offers[0].price_usd if offers else None
            except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
                error = str(e)
            price_cache[price_key] = (cash_price, error)

        if cash_price is None:
            # priced_ok distinguishes a TRANSIENT lookup failure (error set -> the
            # caller should keep the deal queued and retry) from a legitimate
            # "no cash price exists for this route" (error None -> a definitive
            # answer worth recording, not retrying forever). `error` kept for
            # backward compatibility with existing readers.
            results.append({**a, "taxes_usd": taxes_usd, "cash_price": None, "cpp": None,
                             "verdict": "NO CASH PRICE", "priced_ok": error is None,
                             "error": error, "price_error": error})
            continue

        points = int(a["points"])
        if points <= 0:
            # Defensive: price_pending_deals validates points>0 upstream, but never
            # divide by zero here. Definitive (won't be retried).
            results.append({**a, "taxes_usd": taxes_usd, "cash_price": cash_price, "cpp": None,
                             "verdict": "NO CASH PRICE", "priced_ok": True,
                             "error": None, "price_error": None})
            continue

        net = max(cash_price - taxes_usd, 0.0)
        cpp = (net / points) * 100
        verdict = deal_log.verdict_for(cpp, a.get("cabin", ""))
        results.append({**a, "taxes_usd": taxes_usd, "cash_price": cash_price, "cpp": cpp,
                         "verdict": verdict, "priced_ok": True, "error": None, "price_error": None})

    results.sort(key=lambda r: (r["cpp"] is None, -(r["cpp"] or 0)))
    return results


def print_report(results: list[dict]) -> None:
    header = f"{'Route':10} {'Program':28} {'Cabin':9} {'Date':11} {'Pts':>8} {'Taxes$':>8} {'Cash$':>8} {'CPP':>6}  Verdict"
    print(header)
    for r in results:
        cash_s = f"{r['cash_price']:.0f}" if r["cash_price"] is not None else "N/A"
        cpp_s = f"{r['cpp']:.2f}" if r["cpp"] is not None else "N/A"
        route = f"{r['origin']}-{r['dest']}"
        print(f"{route:10} {r['program']:28} {r['cabin']:9} {r['date']:11} "
              f"{r['points']:8,} {r['taxes_usd']:8.2f} {cash_s:>8} {cpp_s:>6}  {r['verdict']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 check_alerts.py alerts.json")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        alerts = json.load(f)
    print_report(evaluate_alerts(alerts))
