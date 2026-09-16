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
    airline_code: str = ""  # IATA marketing carrier, e.g. "LX"


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
    def carrier_codes(self) -> list[str]:
        return [s.airline_code for s in self.segments if s.airline_code]

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
    from fast_flights import FlightQuery, create_query, fetch_flights_html

    query = create_query(
        flights=[FlightQuery(date=departure_date, from_airport=origin, to_airport=destination)],
        seat=_FF_SEAT.get(cabin, "economy"),
        trip="one-way",
        currency="USD",
        language="en",
    )
    return _parse_google_flights_html(fetch_flights_html(query), cabin)


def _parse_google_flights_html(html: str, cabin: str) -> list[FlightOffer]:
    """Parse Google Flights' embedded results payload into FlightOffers.

    Adapted from fast_flights.parser.parse_js, with two fixes: rows missing a
    price are skipped (upstream raises IndexError for the whole page, common on
    first-class searches), and each segment keeps its carrier code and flight
    number (payload index 22: [code, number, _, name]) for award matching, and
    both result lists are read (see below).
    """
    import json as _json
    from datetime import datetime

    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise SearchFailed("Google Flights page had no results payload (possibly blocked).")
    data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return []  # Google's "no flights found"
    payload = _json.loads(data)
    # payload[2][0] = Google's "best flights", payload[3][0] = "other flights".
    # fast-flights reads only [3], silently dropping the best list, which often
    # holds the cheapest fare and the nonstops (found 2026-09-16: JFK-ZRH J
    # cheapest $1,792 and both LX nonstops were only in [2]).
    rows = []
    for idx in (2, 3):
        block = payload[idx] if len(payload) > idx else None
        if isinstance(block, list) and block and isinstance(block[0], list):
            rows.extend(block[0])

    def _hm(v) -> tuple[int, int]:
        padded = [*(v or []), None, None]
        return padded[0] or 0, padded[1] or 0

    def _ts(d, t) -> str:
        (h, mi) = _hm(t)
        return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d} {h:02d}:{mi:02d}"

    offers = []
    for k in rows:
        try:
            price = float(k[1][0][1])
            f = k[0]
            names = f[1] or []
            segments = []
            for sf in f[2]:
                ident = sf[22] if len(sf) > 22 and sf[22] else [None, None, None, None]
                segments.append(FlightSegment(
                    airline=ident[3] or (names[0] if names else "Unknown"),
                    flight_number=f"{ident[0]} {ident[1]}" if ident[0] and ident[1] else "",
                    dep_airport=sf[3], dep_airport_name=sf[4], dep_time=_ts(sf[20], sf[8]),
                    arr_airport=sf[6], arr_airport_name=sf[5], arr_time=_ts(sf[21], sf[10]),
                    duration_minutes=int(sf[11] or 0), airline_code=(ident[0] or "").upper(),
                ))
        except (IndexError, TypeError, KeyError, ValueError):
            continue
        if not segments or price <= 0:
            continue
        layovers = []
        for prev, nxt in zip(segments, segments[1:]):
            try:
                gap = datetime.strptime(nxt.dep_time, "%Y-%m-%d %H:%M") - datetime.strptime(prev.arr_time, "%Y-%m-%d %H:%M")
                mins = max(int(gap.total_seconds() // 60), 0)
            except ValueError:
                mins = 0
            layovers.append(Layover(airport=prev.arr_airport, name=prev.arr_airport_name, duration_minutes=mins))
        offers.append(FlightOffer(
            price_usd=price, cabin=cabin,
            total_duration_minutes=sum(sg.duration_minutes for sg in segments) + sum(l.duration_minutes for l in layovers),
            segments=segments, layovers=layovers, provider="fast-flights",
        ))
    offers.sort(key=lambda o: o.price_usd)
    return offers


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
                airline_code=(seg.get("flight_number") or "").split(" ")[0].upper(),
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
