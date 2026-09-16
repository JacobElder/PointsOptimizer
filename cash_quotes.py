"""
Persistent cash-fare quote store, so each live lookup is paid for once and reused.

Why: 213 priced deals in deal_log.json span only ~41 distinct route+cabin pairs,
yet every alert date used to trigger its own lookup, and flight_search's cache
dies with the process. Backtested on that history (2026-09-16), reusing the
quote from the nearest date on the same route+cabin within +/-7 days had a 1.2%
median error -- so a nearby quote is almost as good as a fresh one.

get_quote() order:
  1. exact route/date/cabin quote, still fresh              -> reused
  2. same route+cabin, date within +/-APPROX_WINDOW_DAYS,
     recent enough                                           -> reused, approx=True
  3. live lookup via flight_search (fast-flights, then SerpApi), saved
Dates outside Google Flights' booking window are never looked up live.

Git-tracked JSON next to deal_log.json so the pipeline and the Streamlit app share it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

import flight_search

_BASE = os.path.dirname(os.path.abspath(__file__))
QUOTES_PATH = os.path.join(_BASE, "cash_quotes.json")

APPROX_WINDOW_DAYS = 7
APPROX_MAX_AGE_DAYS = 14
# Google Flights only lists fares ~11 months ahead; every "no cash price" in
# deal_log.json with no error was 343-357 days out, the furthest priced was 329.
MAX_LOOKAHEAD_DAYS = 330


@dataclass
class Quote:
    origin: str
    dest: str
    cabin: str
    date: str
    price_usd: float | None  # None = provider answered "no itineraries"
    nonstop_price_usd: float | None
    provider: str
    fetched_at: str
    approx: bool = False  # True when borrowed from a nearby date
    # Up to MAX_STORED_OFFERS cheapest itineraries: [price, stops, "LX,LX"].
    # Older records lack this; comparable_fare() then falls back to the two prices above.
    offers: list | None = None


MAX_STORED_OFFERS = 25


@dataclass
class Comparable:
    price: float
    basis: str  # human-readable: what the cash fare is
    same_carrier_price: float | None  # cheapest fare on the award's own airline(s), for reference
    nonstop_price: float | None = None  # cheapest nonstop fare, for reference


def _codes(carriers: str | None) -> set[str]:
    return {c.strip().upper() for c in (carriers or "").replace(";", ",").split(",") if c.strip()}


def comparable_fare(quote: "Quote", direct: bool | None = None, carriers: str | None = None) -> Comparable | None:
    """The cash fare an award should be valued against: what you'd realistically pay instead.

    - Nonstop award: cheapest fare with AT MOST ONE stop. Not the cheapest
      nonstop: one-way nonstop fares on legacy carriers are often absurd
      (JFK-ZRH Swiss J $8,919 vs $1,468 with one stop, 2026-09-16), and using
      them produced 8-15c "deals" nobody would pay cash for. But a 2-3 stop
      fare isn't an equivalent trip either.
    - Any other award: cheapest fare, any stops.
    The nonstop fare and the award airline's own fare are reported for context
    only, never used for CPP.
    """
    if quote.price_usd is None:
        return None
    wanted = _codes(carriers)
    offers = quote.offers or []
    if not offers:  # legacy record: only the two headline prices were stored
        return Comparable(quote.price_usd, "cheapest fare, any stops", None, quote.nonstop_price_usd)
    if direct:
        pool = [o for o in offers if o[1] <= 1] or offers
        basis = "cheapest fare with at most 1 stop"
    else:
        pool, basis = offers, "cheapest fare, any stops"
    same = [o[0] for o in offers if wanted & _codes(o[2])] if wanted else []
    nonstop = [o[0] for o in offers if o[1] == 0]
    return Comparable(min(o[0] for o in pool), basis, min(same) if same else None,
                      min(nonstop) if nonstop else None)


class OutOfWindow(Exception):
    """Travel date is in the past or beyond Google Flights' booking window."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load() -> list[dict]:
    try:
        with open(QUOTES_PATH) as f:
            return json.load(f).get("quotes", [])
    except (OSError, json.JSONDecodeError):
        return []


def save(quotes: list[dict]) -> None:
    cutoff = _now().date().isoformat()
    quotes = [q for q in quotes if q["date"] >= cutoff]  # past-dated quotes are useless
    quotes = sorted(quotes, key=lambda q: (q["origin"], q["dest"], q["cabin"], q["date"]))
    with open(QUOTES_PATH, "w") as f:
        json.dump({"quotes": quotes}, f, indent=1)
        f.write("\n")


def _max_age_days(travel_day: date, today: date) -> int:
    return 2 if (travel_day - today).days <= 30 else 7


def _age_days(q: dict) -> float:
    fetched = datetime.strptime(q["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (_now() - fetched).total_seconds() / 86400


def check_window(travel_date: str, today: date | None = None) -> None:
    today = today or _now().date()
    days_out = (_parse_day(travel_date) - today).days
    if days_out < 0:
        raise OutOfWindow(f"{travel_date} is in the past")
    if days_out > MAX_LOOKAHEAD_DAYS:
        raise OutOfWindow(f"{travel_date} is {days_out} days out; Google Flights only lists ~{MAX_LOOKAHEAD_DAYS}")


def find_cached(quotes: list[dict], origin: str, dest: str, travel_date: str, cabin: str,
                allow_approx: bool = True) -> Quote | None:
    origin, dest, cabin = origin.upper(), dest.upper(), cabin.upper()
    today = _now().date()
    travel_day = _parse_day(travel_date)
    same_route = [q for q in quotes if q["origin"] == origin and q["dest"] == dest and q["cabin"] == cabin]

    for q in same_route:
        if q["date"] == travel_date and _age_days(q) <= _max_age_days(travel_day, today):
            return Quote(**{**q, "approx": False})

    if not allow_approx:
        return None
    nearby = [
        q for q in same_route
        if q.get("price_usd") is not None
        and 0 < abs((_parse_day(q["date"]) - travel_day).days) <= APPROX_WINDOW_DAYS
        and _age_days(q) <= APPROX_MAX_AGE_DAYS
    ]
    if not nearby:
        return None
    best = min(nearby, key=lambda q: (abs((_parse_day(q["date"]) - travel_day).days), _age_days(q)))
    return Quote(**{**best, "approx": True})


def get_quote(origin: str, dest: str, travel_date: str, cabin: str, *,
              allow_approx: bool = True, allow_live: bool = True) -> Quote | None:
    """Best available quote. Returns None only if allow_live=False and nothing cached.

    Raises OutOfWindow (no lookup attempted), or flight_search's NotConfigured /
    QuotaExhausted / SearchFailed from the live lookup.
    """
    origin, dest, cabin = origin.strip().upper(), dest.strip().upper(), cabin.strip().upper()
    quotes = load()
    cached = find_cached(quotes, origin, dest, travel_date, cabin, allow_approx=allow_approx)
    if cached is not None:
        return cached
    if not allow_live:
        return None
    check_window(travel_date)

    offers = flight_search.search_cash_price(origin, dest, travel_date, cabin, max_results=100)
    nonstop = [o.price_usd for o in offers if o.stops == 0]
    quote = Quote(
        origin=origin, dest=dest, cabin=cabin, date=travel_date,
        price_usd=offers[0].price_usd if offers else None,
        nonstop_price_usd=min(nonstop) if nonstop else None,
        provider=offers[0].provider if offers else ("fast-flights" if flight_search.fast_flights_available() else "serpapi"),
        fetched_at=_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        offers=[[o.price_usd, o.stops, ",".join(o.carrier_codes)] for o in offers[:MAX_STORED_OFFERS]],
    )
    record = {k: v for k, v in asdict(quote).items() if k != "approx"}
    quotes = [q for q in load() if not (q["origin"] == origin and q["dest"] == dest
                                        and q["cabin"] == cabin and q["date"] == travel_date)]
    quotes.append(record)
    save(quotes)
    return quote
