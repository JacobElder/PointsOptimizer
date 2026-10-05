"""
Flight-level detail for an award (seats.aero Get Trips, 1 API call each).

Cached Search only says "business is available for N points on this date". The
trips behind it show which flights, how long, where it connects, the cabin of
every leg (a "business" award can include an economy leg: mixed cabin), and a
direct booking link for the program.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

import seats_aero

TRIPS_URL = "https://seats.aero/partnerapi/trips/{id}"


class QuotaExhausted(Exception):
    """seats.aero rate-limited us (429). Every further call this run would fail
    too, so the caller stops instead of burning the rest of a shared daily quota
    on lookups that cannot succeed."""


class LookupFailed(Exception):
    """The trip lookup itself failed (network, rate limit, bad response).

    Distinct from returning None, which means seats.aero answered and the award
    is no longer offered in that cabin. Treating a failure as "gone" would
    silently empty a digest whenever seats.aero rate-limits the tail of a run.
    """
_CABIN_RANK = {"economy": 0, "premium": 1, "business": 2, "first": 3}
_SEATS_CABIN = {"ECONOMY": "economy", "PREMIUM_ECONOMY": "premium", "BUSINESS": "business", "FIRST": "first"}


@dataclass
class TripInfo:
    flights: list[str]  # "UA40 EWR–FCO"
    connections: list[str]  # airport codes
    duration_min: int
    departs_at: str  # "2026-10-22T17:15:00Z" (local time as published by seats.aero)
    arrives_at: str
    leg_cabins: list[str]
    mixed_cabin: bool  # some leg is in a lower cabin than the award's
    lower_cabin_legs: list[str]  # "AC932 YUL–CTA (economy)"
    carriers: str  # "United, ITA Airways"
    booking_url: str | None
    booking_label: str | None
    other_itineraries: int  # other flight options at the same price
    airport_changes: list[str] = None  # "DCA → IAD": land at one airport, depart from another
    # Re-verification against seats.aero right now (the scan's data can be days old):
    stops: int = 0
    nonstop: bool = False  # ONE segment. seats.aero's "direct" flag only means one flight number.
    seats: int = 0
    current_points: int = 0  # cheapest price for this cabin now
    price_matches: bool = True  # still bookable at the points the scan reported

    def as_dict(self) -> dict:
        return asdict(self)


_EMIRATES_CABIN = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}


def _with_cabin(url: str | None, cabin: str) -> str | None:
    """seats.aero's Emirates link encodes the search as base64 JSON and always asks for
    economy ("cabinClass": "Y"), so "Book via Emirates" on the Oct 5 EWR-ATH business
    card opened an economy search. Point it at the award's cabin. Anything that
    doesn't parse as expected is returned untouched."""
    if not url or "emirates.com" not in url or "searchRequest=" not in url:
        return url
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        req = json.loads(base64.b64decode(query["searchRequest"][0]))
        for seg in req.get("segments", []):
            seg["cabinClass"] = _EMIRATES_CABIN.get(cabin, seg.get("cabinClass"))
        query["searchRequest"] = [base64.b64encode(json.dumps(req, separators=(",", ":")).encode()).decode()]
        return urlunsplit(parts._replace(query=urlencode(query, doseq=True)))
    except (KeyError, ValueError, TypeError):
        return url


def fetch(availability_id: str, cabin: str, points: int, session: requests.Session | None = None) -> TripInfo | None:
    """Best (shortest) itinerary for this award's cabin at its price. None if unavailable."""
    if not availability_id:
        return None
    http = session or requests
    try:
        resp = http.get(TRIPS_URL.format(id=availability_id),
                        headers={"Partner-Authorization": seats_aero._get_api_key()}, timeout=30)
        if resp.status_code == 429:
            raise QuotaExhausted("seats.aero returned 429 (daily quota or rate limit)")
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError, seats_aero.NotConfigured) as e:
        raise LookupFailed(str(e)) from e
    want = _SEATS_CABIN.get(cabin.upper(), "economy")
    trips = [t for t in payload.get("data", []) if t.get("Cabin") == want]
    if not trips:
        return None  # award no longer offered in this cabin
    cheapest_now = min(int(t.get("MileageCost") or 0) for t in trips)
    # Allow the price to have improved, but never report a stale cheaper price.
    at_price = [t for t in trips if int(t.get("MileageCost") or 0) <= int(points)] or trips
    at_price.sort(key=lambda t: (int(t.get("MileageCost") or 0), int(t.get("TotalDuration") or 0)))
    t = at_price[0]
    segs = t.get("AvailabilitySegments") or []
    names = payload.get("carriers") or {}
    flights = [f"{s.get('FlightNumber')} {s.get('OriginAirport')}–{s.get('DestinationAirport')}" for s in segs]
    leg_cabins = [s.get("Cabin") or want for s in segs]
    lower = [f"{f} ({c})" for f, c in zip(flights, leg_cabins) if _CABIN_RANK.get(c, 0) < _CABIN_RANK[want]]
    carriers = []
    for code in (t.get("Carriers") or "").replace(" ", "").split(","):
        name = names.get(code) or code
        if code and name not in carriers:
            carriers.append(name)
    links = payload.get("booking_links") or []
    link = next((l for l in links if l.get("primary")), links[0] if links else None)
    changes = [f"{a.get('DestinationAirport')} → {b.get('OriginAirport')}" for a, b in zip(segs, segs[1:])
               if a.get("DestinationAirport") and b.get("OriginAirport")
               and a.get("DestinationAirport") != b.get("OriginAirport")]
    return TripInfo(
        flights=flights,
        connections=list(t.get("Connections") or []),
        duration_min=int(t.get("TotalDuration") or 0),
        departs_at=t.get("DepartsAt", ""),
        arrives_at=t.get("ArrivesAt", ""),
        leg_cabins=leg_cabins,
        mixed_cabin=bool(lower),
        lower_cabin_legs=lower,
        carriers=", ".join(carriers),
        booking_url=_with_cabin(link.get("link"), want) if link else None,
        booking_label=link.get("label") if link else None,
        other_itineraries=len(at_price) - 1,
        airport_changes=changes,
        stops=max(len(segs) - 1, 0),
        nonstop=len(segs) == 1,
        seats=int(t.get("RemainingSeats") or 0),
        current_points=cheapest_now,
        price_matches=cheapest_now <= int(points),
    )
