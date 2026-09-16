"""
Batch-evaluate seats.aero alert emails against live cash prices.

seats.aero alerts tell you a route clears your points/fee thresholds, but not
whether it's actually good value in cents-per-point terms — that still needs a
live cash price for the same route/date/cabin. This script closes that gap:
feed it a JSON list of parsed alerts (see seats_aero_alerts.parse_alert_email
for how to produce one from raw alert email HTML) and it looks up each route's
live cash price via flight_search, computes CPP, and ranks the results.

Fetching + parsing the emails happens in a Claude Code routine with Gmail
access; this script is the second half of that pipeline. Cash prices come from
cash_quotes (persistent, reused across nearby dates on the same route+cabin).

Usage:
    python3 check_alerts.py alerts.json

alerts.json is a list of objects with: origin, dest, program, cabin, date,
points, taxes, currency (default "USD").
"""

from __future__ import annotations

import json
import sys

import requests

import cash_quotes
import deal_log
import flight_search

# Used only if the live rate lookup below fails (offline, API down, etc).
_FX_FALLBACK = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27}

# Verdict thresholds live in deal_log (cabin-aware verdict_for()).
SKIP_FLOOR = deal_log.SKIP_CPP

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


def compute_cpp(cash_price: float, taxes_usd: float, points: int) -> float | None:
    """Cents per point: (cash fare - award taxes) / points * 100. None if points <= 0."""
    if points <= 0:
        return None
    return (max(cash_price - taxes_usd, 0.0) / points) * 100


def evaluate_alerts(alerts: list[dict], allow_live: bool = True) -> list[dict]:
    """Price each alert and compute CPP + verdict.

    Each result carries `price_status`:
      "priced"         -- cash price found, cpp/verdict set
      "no_fare"        -- provider answered: no itineraries (definitive for now)
      "out_of_window"  -- date past or beyond the booking window; no lookup made
      "quota"          -- SerpApi quota exhausted; later alerts in the batch skip live lookups
      "failed"         -- transient lookup error, worth retrying
      "not_cached"     -- allow_live=False and nothing usable cached
    `priced_ok` stays for older readers: False only for transient states.
    """
    results = []
    quota_hit = False
    for a in alerts:
        taxes_usd = float(a["taxes"]) * _fx_rate(a.get("currency", "USD"))
        base = {**a, "taxes_usd": taxes_usd, "cash_price": None, "cpp": None, "verdict": "NO CASH PRICE",
                "error": None, "price_error": None}
        status, quote = "priced", None
        try:
            quote = cash_quotes.get_quote(a["origin"], a["dest"], a["date"], a["cabin"],
                                          allow_live=allow_live and not quota_hit)
            if quote is None:
                status = "quota" if quota_hit else "not_cached"
            elif quote.price_usd is None:
                status = "no_fare"
        except cash_quotes.OutOfWindow as e:
            status, base["error"] = "out_of_window", str(e)
        except flight_search.QuotaExhausted as e:
            quota_hit = True
            status, base["error"] = "quota", str(e)
        except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
            status, base["error"] = "failed", str(e)
        base["price_error"] = base["error"]

        if status != "priced":
            results.append({**base, "price_status": status,
                            "priced_ok": status in ("no_fare",)})
            continue

        cpp = compute_cpp(quote.price_usd, taxes_usd, int(a["points"]))
        results.append({**base, "cash_price": quote.price_usd, "cpp": cpp,
                        "nonstop_cash_price": quote.nonstop_price_usd,
                        "cash_provider": quote.provider, "cash_is_approx": quote.approx,
                        "cash_quote_date": quote.date,
                        "verdict": deal_log.verdict_for(cpp, a.get("cabin", "")),
                        "price_status": "priced", "priced_ok": True})

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
