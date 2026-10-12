"""
Live cash-price lookups (Google Flights data) via fast-flights, which scrapes
Google Flights directly: free, no key. If it isn't installed or its import fails
(e.g. a protobuf version clash), lookups raise NotConfigured.

SerpApi (a paid Google Flights API) was the fallback until 2026-10-12. It served
the same Google prices, almost never ran once fast-flights became primary, and was
removed at the user's request.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# Process-level memoization: identical lookups in one process (a double-clicked
# search, several awards sharing a route/date) reuse the first answer instead of
# asking Google again. Keyed on the normalized (origin, destination,
# departure_date, cabin); max_results only slices the cached full list.
_CACHE: dict[tuple[str, str, str, str], list["FlightOffer"]] = {}


def clear_cache() -> None:
    """Empty the in-process price cache. Safe to call repeatedly. Exposed so
    tests stay deterministic and callers can force a fresh lookup if needed."""
    _CACHE.clear()


class NotConfigured(Exception):
    """Raised when fast-flights isn't installed or can't be imported."""


class SearchFailed(Exception):
    """Raised when the live search fails. Message is safe to display."""


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
    provider: str = "fast-flights"

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


# Set to True (e.g. by tests) so nothing reaches Google.
FAST_FLIGHTS_DISABLED = os.environ.get("POINTSOPT_DISABLE_FAST_FLIGHTS") == "1"


def fast_flights_available() -> bool:
    if FAST_FLIGHTS_DISABLED:
        return False
    try:
        import fast_flights  # noqa: F401
        return True
    except Exception:  # ImportError, or protobuf VersionError on a clashing env
        return False


def search_cash_price(
    origin: str,
    destination: str,
    departure_date: str,
    cabin: str = "ECONOMY",
    max_results: int = 5,
) -> list[FlightOffer]:
    """
    Query live one-way cash prices for a route/date/cabin via Google Flights, cheapest first.

    Raises NotConfigured if fast-flights is unusable, or SearchFailed if the
    lookup fails. Successful, non-empty lookups are memoized (see _CACHE).
    """
    # Normalize so the cache key matches regardless of caller casing/whitespace.
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    cabin = cabin.strip().upper()
    key = (origin, destination, departure_date, cabin)

    cached = _CACHE.get(key)
    if cached is not None:
        # Cached list is the full fetched result; slice to this call's max_results.
        return cached[:max_results]

    if not fast_flights_available():
        raise NotConfigured("Cash prices need the fast-flights package (pip install -r requirements.txt).")
    try:
        offers = _fetch_offers_fast_flights(origin, destination, departure_date, cabin)
    except SearchFailed:
        raise
    except Exception as e:  # scraper breakage, network, Google blocking
        raise SearchFailed(f"Google Flights lookup failed ({type(e).__name__}).")

    # Only cache SUCCESSFUL, NON-EMPTY results. Failures (NotConfigured /
    # SearchFailed) propagate before this point, so
    # a transient failure stays retryable. We also skip caching empty responses so
    # a later retry can pick up newly-available inventory instead of being pinned
    # to "no flights" for the life of the process.
    if offers:
        _CACHE[key] = offers
    return offers[:max_results]


_FF_SEAT = {"ECONOMY": "economy", "PREMIUM_ECONOMY": "premium-economy", "BUSINESS": "business", "FIRST": "first"}

# Google throttles the scraper in bursts: on Oct 10 and 11 2026 a third of the
# round-trip checks came back empty, the empties bunched in the busiest minute
# (Oct 11 15:02: 78 lookups, 20 empty) while lookups ran back to back, and the
# end-of-run retry, spaced 2 s apart, recovered 8 of 10. So after an empty or
# failed answer the next lookup waits, longer while the empties continue, and a
# real answer resets it. A real "no flights" costs one 3 s pause. Off unless a
# batch run turns it on (reset_backoff), so the site never stalls on it.
BACKOFF_FIRST_S = 3.0
BACKOFF_MAX_S = 24.0
BACKOFF_RUN_TOTAL_S = 240.0  # a hard block must not add half an hour to the run

_backoff = {"enabled": False, "next_s": 0.0, "waited_s": 0.0, "pauses": 0}
_lookups: list[tuple[str, str, bool]] = []  # (UTC minute, "one-way"/"round-trip", got fares)
_sleep = time.sleep


def reset_backoff(enabled: bool) -> None:
    """Start a run: clear the pause state and the lookup log."""
    _backoff.update(enabled=enabled, next_s=0.0, waited_s=0.0, pauses=0)
    _lookups.clear()


def backoff_stats() -> dict:
    return {"pauses": _backoff["pauses"], "waited_s": round(_backoff["waited_s"], 1)}


def lookup_log() -> list[tuple[str, str, bool]]:
    return list(_lookups)


def _fetch_html(query) -> str:
    from fast_flights import fetch_flights_html
    return fetch_flights_html(query)


def _google(query, cabin: str, kind: str) -> list[FlightOffer]:
    """One fast-flights lookup, paced after empty answers (see BACKOFF_FIRST_S)."""
    wait = min(_backoff["next_s"], BACKOFF_RUN_TOTAL_S - _backoff["waited_s"])
    if _backoff["enabled"] and wait > 0:
        _sleep(wait)
        _backoff["waited_s"] += wait
        _backoff["pauses"] += 1
    offers: list[FlightOffer] = []
    try:
        offers = _parse_google_flights_html(_fetch_html(query), cabin)
        return offers
    finally:
        _lookups.append((datetime.now(timezone.utc).strftime("%H:%M"), kind, bool(offers)))
        _backoff["next_s"] = 0.0 if offers else min(max(_backoff["next_s"] * 2, BACKOFF_FIRST_S), BACKOFF_MAX_S)


def _fetch_offers_fast_flights(origin: str, destination: str, departure_date: str, cabin: str) -> list[FlightOffer]:
    """Free Google Flights lookup via fast-flights. Raises on any failure;
    returns [] when Google genuinely has no itineraries (e.g. date too far out)."""
    from fast_flights import FlightQuery, create_query

    query = create_query(
        flights=[FlightQuery(date=departure_date, from_airport=origin, to_airport=destination)],
        seat=_FF_SEAT.get(cabin, "economy"),
        trip="one-way",
        currency="USD",
        language="en",
    )
    return _google(query, cabin, "one-way")


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


def search_round_trip_offers(origin: str, destination: str, depart_date: str, return_date: str,
                             cabin: str = "ECONOMY") -> list[FlightOffer]:
    """Round-trip fares from Google Flights.

    Each offer's price_usd is the ROUND-TRIP TOTAL; segments/stops describe the
    outbound leg. Raises SearchFailed if fast-flights is unavailable or fails.
    """
    if not fast_flights_available():
        raise SearchFailed("Round-trip check needs fast-flights.")
    from fast_flights import FlightQuery, create_query

    origin, destination, cabin = origin.strip().upper(), destination.strip().upper(), cabin.strip().upper()
    query = create_query(
        flights=[FlightQuery(date=depart_date, from_airport=origin, to_airport=destination),
                 FlightQuery(date=return_date, from_airport=destination, to_airport=origin)],
        seat=_FF_SEAT.get(cabin, "economy"),
        trip="round-trip",
        currency="USD",
        language="en",
    )
    try:
        return _google(query, cabin, "round-trip")
    except SearchFailed:
        raise
    except Exception as e:
        raise SearchFailed(f"Round-trip lookup failed ({type(e).__name__}).")

