"""
Live one-way cash-price lookups (Google Flights data), from two providers:

1. fast-flights (free, no key) -- scrapes Google Flights directly. Primary.
   Optional dependency: if it isn't installed or its import fails (e.g. a
   protobuf version clash), it's silently skipped.
2. SerpApi's Google Flights engine (SERPAPI_KEY; 250 searches/month free) --
   fallback, used only when fast-flights errors out (not when it returns an
   empty result, which is a real answer and not worth a paid lookup).

Both return the same Google Flights prices, so CPPs are comparable across them.
Configure SerpApi via environment variable or Streamlit secrets: SERPAPI_KEY
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

SEARCH_URL = "https://serpapi.com/search"

_TRAVEL_CLASS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}

# FIX 8 — process-level memoization for quota safety.
# The SerpApi free tier allows only 250 searches/MONTH, so redundant identical
# lookups (double-clicked "Search live prices", several awards sharing a
# route/date, Deal Radar's per-button return valuation) must not each burn a
# call. We cache the FULL offer list keyed on the normalized
# (origin, destination, departure_date, cabin) tuple — the exact inputs that
# determine the SerpApi request. max_results is deliberately NOT part of the key:
# it only slices already-fetched results and does not change the API call, so we
# cache the fuller list and slice to max_results on return. Worst case, a later
# call with a larger max_results gets fewer rows than a fresh fetch could — never
# wrong prices, and never an extra request, which is the right trade for quota.
_CACHE: dict[tuple[str, str, str, str], list["FlightOffer"]] = {}


def clear_cache() -> None:
    """Empty the in-process price cache. Safe to call repeatedly. Exposed so
    tests stay deterministic and callers can force a fresh lookup if needed."""
    _CACHE.clear()


class NotConfigured(Exception):
    """Raised when a SerpApi key isn't set."""


class SearchFailed(Exception):
    """Raised when the live search fails. Message is safe to display — it never
    contains the request URL, which embeds the API key as a query parameter."""


class QuotaExhausted(SearchFailed):
    """SerpApi rejected the call because the account's monthly quota is spent.
    Distinct from a transient failure: retrying this month only wastes runs."""


@dataclass
class FlightSegment:
    airline: str
    flight_number: str
    dep_airport: str
    dep_airport_name: str
    dep_time: str  # "YYYY-MM-DD HH:MM"
    arr_airport: str
    arr_airport_name: str
    arr_time: str
    duration_minutes: int


@dataclass
class Layover:
    airport: str
    name: str
    duration_minutes: int


@dataclass
class FlightOffer:
    price_usd: float
    cabin: str
    total_duration_minutes: int
    segments: list[FlightSegment]
    layovers: list[Layover]
    provider: str = "serpapi"

    @property
    def airline(self) -> str:
        return self.segments[0].airline if self.segments else "Unknown"

    @property
    def stops(self) -> int:
        return len(self.layovers)

    @property
    def origin(self) -> str:
        return self.segments[0].dep_airport if self.segments else ""

    @property
    def destination(self) -> str:
        return self.segments[-1].arr_airport if self.segments else ""

    @property
    def departure_time(self) -> str:
        return self.segments[0].dep_time if self.segments else ""

    @property
    def arrival_time(self) -> str:
        return self.segments[-1].arr_time if self.segments else ""


def _get_api_key() -> str:
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        try:
            import streamlit as st

            key = st.secrets.get("SERPAPI_KEY")
        except Exception:
            pass
    if not key:
        raise NotConfigured(
            "Not configured. Get a free API key at https://serpapi.com (self-serve, 250 "
            "searches/month free), then set SERPAPI_KEY as an environment variable or in "
            ".streamlit/secrets.toml and restart the app."
        )
    return key


def serpapi_configured() -> bool:
    try:
        _get_api_key()
        return True
    except NotConfigured:
        return False


# Set to True (e.g. by tests) to force the SerpApi-only path.
FAST_FLIGHTS_DISABLED = os.environ.get("POINTSOPT_DISABLE_FAST_FLIGHTS") == "1"


def fast_flights_available() -> bool:
    if FAST_FLIGHTS_DISABLED:
        return False
    try:
        import fast_flights  # noqa: F401
        return True
    except Exception:  # ImportError, or protobuf VersionError on a clashing env
        return False


def is_configured() -> bool:
    """Whether ANY cash-price provider is usable."""
    return fast_flights_available() or serpapi_configured()


def search_cash_price(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str = "ECONOMY",
    max_results: int = 5,
) -> list[FlightOffer]:
    """
    Query live one-way cash prices for a route/date/cabin via Google Flights, cheapest first.

    Tries fast-flights first, then SerpApi if fast-flights fails. Raises
    NotConfigured if no provider is usable, QuotaExhausted if SerpApi was needed
    but its quota is spent, or SearchFailed if every usable provider failed.

    Successful, non-empty lookups are memoized (see _CACHE) so repeated identical
    (origin, destination, departure_date, cabin) requests reuse the first result
    instead of burning another SerpApi call.
    """
    # Normalize exactly the way the API request builder does below, so the cache
    # key matches regardless of caller casing/whitespace.
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    cabin = cabin.strip().upper()
    key = (origin, destination, departure_date, cabin)

    cached = _CACHE.get(key)
    if cached is not None:
        # Cached list is the full fetched result; slice to this call's max_results.
        return cached[:max_results]

    offers = _fetch_with_fallback(origin, destination, departure_date, cabin)

    # Only cache SUCCESSFUL, NON-EMPTY results. Failures (NotConfigured /
    # SearchFailed) propagate out of _fetch_offers and are never reached here, so
    # a transient failure stays retryable. We also skip caching empty responses so
    # a later retry can pick up newly-available inventory instead of being pinned
    # to "no flights" for the life of the process.
    if offers:
        _CACHE[key] = offers
    return offers[:max_results]


# Per-process cap on paid SerpApi calls (None = unlimited). Bulk jobs set this so a
# fast-flights outage can't silently drain the monthly quota through the fallback.
SERPAPI_MAX_CALLS: int | None = None
serpapi_calls_made = 0


def _fetch_with_fallback(origin: str, destination: str, departure_date: str, cabin: str) -> list[FlightOffer]:
    ff_error: Exception | None = None
    if fast_flights_available():
        try:
            return _fetch_offers_fast_flights(origin, destination, departure_date, cabin)
        except Exception as e:  # scraper breakage, network, Google blocking
            ff_error = e
    if not serpapi_configured():
        if ff_error is not None:
            raise SearchFailed(f"Free Google Flights lookup failed ({type(ff_error).__name__}) and no SerpApi key is set.")
        _get_api_key()  # raises NotConfigured with setup instructions
    global serpapi_calls_made
    if SERPAPI_MAX_CALLS is not None and serpapi_calls_made >= SERPAPI_MAX_CALLS:
        raise QuotaExhausted(f"Per-run SerpApi cap ({SERPAPI_MAX_CALLS}) reached"
                             + (f"; free lookup failed ({type(ff_error).__name__})" if ff_error else ""))
    serpapi_calls_made += 1
    return _fetch_offers(origin, destination, departure_date, cabin)


_FF_SEAT = {"ECONOMY": "economy", "PREMIUM_ECONOMY": "premium-economy", "BUSINESS": "business", "FIRST": "first"}


def _fetch_offers_fast_flights(origin: str, destination: str, departure_date: str, cabin: str) -> list[FlightOffer]:
    """Free Google Flights lookup via fast-flights. Raises on any failure;
    returns [] when Google genuinely has no itineraries (e.g. date too far out)."""
    from datetime import datetime

    from fast_flights import FlightQuery, FlightsNotFound, create_query, fetch_flights_html

    query = create_query(
        flights=[FlightQuery(date=departure_date, from_airport=origin, to_airport=destination)],
        seat=_FF_SEAT.get(cabin, "economy"),
        trip="one-way",
        currency="USD",
        language="en",
    )
    html = fetch_flights_html(query)
    try:
        results = _parse_fast_flights_html(html)
    except FlightsNotFound:
        return []

    def _ts(sdt) -> str:
        (y, mo, d), (h, mi) = sdt.date, sdt.time
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"

    offers = []
    for f in results:
        if not f.flights or not f.price:
            continue
        airline = f.airlines[0] if f.airlines else "Unknown"
        segments = [
            FlightSegment(
                airline=airline, flight_number="",
                dep_airport=sf.from_airport.code, dep_airport_name=sf.from_airport.name,
                dep_time=_ts(sf.departure),
                arr_airport=sf.to_airport.code, arr_airport_name=sf.to_airport.name,
                arr_time=_ts(sf.arrival), duration_minutes=int(sf.duration or 0),
            )
            for sf in f.flights
        ]
        layovers = []
        for prev, nxt in zip(segments, segments[1:]):
            try:
                gap = datetime.strptime(nxt.dep_time, "%Y-%m-%d %H:%M") - datetime.strptime(prev.arr_time, "%Y-%m-%d %H:%M")
                mins = max(int(gap.total_seconds() // 60), 0)
            except ValueError:
                mins = 0
            layovers.append(Layover(airport=prev.arr_airport, name=prev.arr_airport_name, duration_minutes=mins))
        offers.append(FlightOffer(
            price_usd=float(f.price), cabin=cabin,
            total_duration_minutes=sum(sg.duration_minutes for sg in segments) + sum(l.duration_minutes for l in layovers),
            segments=segments, layovers=layovers, provider="fast-flights",
        ))
    offers.sort(key=lambda o: o.price_usd)
    return offers


def _parse_fast_flights_html(html: str) -> list:
    """fast-flights' own parser, made tolerant of itineraries with no price.

    fast_flights.parser.parse_js indexes k[1][0][1] unguarded and raises
    IndexError for the whole result set when any single row lacks a price
    (common on first-class searches). Same logic, but bad rows are skipped.
    """
    from fast_flights import parser as ffp

    try:
        return ffp.parse(html)
    except (IndexError, TypeError):
        pass
    import json as _json

    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise SearchFailed("Google Flights page had no results payload (possibly blocked).")
    js = script.text()
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []
    payload = _json.loads(data)
    rows = (payload[3] or [None])[0] if len(payload) > 3 else None
    flights = []
    for k in rows or []:
        try:
            price = k[1][0][1]
            f = k[0]
            segs = [
                ffp.SingleFlight(
                    from_airport=ffp.Airport(code=sf[3], name=sf[4]),
                    to_airport=ffp.Airport(code=sf[6], name=sf[5]),
                    departure=ffp.SimpleDatetime(date=tuple(sf[20]), time=ffp._parse_time(sf[8])),
                    arrival=ffp.SimpleDatetime(date=tuple(sf[21]), time=ffp._parse_time(sf[10])),
                    duration=sf[11],
                    plane_type=sf[17],
                )
                for sf in f[2]
            ]
            flights.append(ffp.Flights(type=f[0], price=price, airlines=f[1], flights=segs,
                                       carbon=ffp.CarbonEmission(typical_on_route=0, emission=0)))
        except (IndexError, TypeError, KeyError):
            continue
    return flights


def serpapi_account_remaining() -> int | None:
    """Searches left this month per SerpApi's own account endpoint (free; does
    not consume quota). None if unknown. Authoritative, unlike any local counter."""
    try:
        resp = requests.get("https://serpapi.com/account.json", params={"api_key": _get_api_key()}, timeout=10)
        resp.raise_for_status()
        return int(resp.json().get("total_searches_left"))
    except Exception:
        return None


def _fetch_offers(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str,
) -> list[FlightOffer]:
    """Perform the live SerpApi lookup and parse it into the full, sorted offer
    list (unsliced). Inputs are assumed already normalized (upper/stripped)."""
    api_key = _get_api_key()
    try:
        resp = requests.get(
            SEARCH_URL,
            params={
                "engine": "google_flights",
                "departure_id": origin.upper(),
                "arrival_id": destination.upper(),
                "outbound_date": departure_date,
                "type": 2,  # one way
                "travel_class": _TRAVEL_CLASS.get(cabin.upper(), 1),
                "currency": "USD",
                "hl": "en",
                "api_key": api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError:
        if resp.status_code == 429:
            raise QuotaExhausted("SerpApi monthly search quota is used up (HTTP 429).")
        raise SearchFailed(f"SerpApi returned HTTP {resp.status_code}. Check your key/quota at serpapi.com.")
    except requests.RequestException as e:
        raise SearchFailed(f"Network error during search: {type(e).__name__}. Try again.")
    except ValueError:
        raise SearchFailed("SerpApi returned an unreadable response. Try again.")

    offers = []
    for item in payload.get("best_flights", []) + payload.get("other_flights", []):
        raw_segments = item.get("flights", [])
        if not raw_segments or item.get("price") is None:
            continue

        segments = [
            FlightSegment(
                airline=seg.get("airline", "Unknown"),
                flight_number=seg.get("flight_number", ""),
                dep_airport=seg.get("departure_airport", {}).get("id", ""),
                dep_airport_name=seg.get("departure_airport", {}).get("name", ""),
                dep_time=seg.get("departure_airport", {}).get("time", ""),
                arr_airport=seg.get("arrival_airport", {}).get("id", ""),
                arr_airport_name=seg.get("arrival_airport", {}).get("name", ""),
                arr_time=seg.get("arrival_airport", {}).get("time", ""),
                duration_minutes=seg.get("duration", 0),
            )
            for seg in raw_segments
        ]
        layovers = [
            Layover(
                airport=lay.get("id", ""),
                name=lay.get("name", ""),
                duration_minutes=lay.get("duration", 0),
            )
            for lay in item.get("layovers", [])
        ]

        # A single malformed price (non-numeric, unexpected type) must skip only
        # that row, not abort the whole result set.
        try:
            price_usd = float(item["price"])
        except (TypeError, ValueError):
            continue

        offers.append(
            FlightOffer(
                price_usd=price_usd,
                cabin=cabin.upper(),
                total_duration_minutes=item.get("total_duration", sum(s.duration_minutes for s in segments)),
                segments=segments,
                layovers=layovers,
            )
        )
    offers.sort(key=lambda o: o.price_usd)
    return offers
