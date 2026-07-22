"""
Optional live award-availability lookups via seats.aero's Partner API (Cached Search).

Pro subscription required: https://seats.aero — self-serve, includes a developer API
key good for 1,000 calls/day (tracked via the x-ratelimit-* response headers, not a
per-minute throttle). Live Search is a separate, more expensive endpoint not available
on the Pro tier — this module only ever calls Cached Search, which is free with the
subscription and fits an on-demand "check this deal" button rather than any polling.

Auto-fills the *points required* + *taxes* side of a CPP calculation, the same way
flight_search.py auto-fills the cash-price side. The Flight Analyzer page falls back
to manual entry if this isn't configured.

Configure via environment variable or Streamlit secrets:
    SEATS_AERO_API_KEY

Coverage note: seats.aero tracks major alliance/transfer-partner programs but not
every program in cards_data.py. Verified live on 2026-07-22: covered — Aeroplan,
Aeromexico Rewards, British Airways, Etihad, Finnair, Flying Blue, Iberia, JetBlue,
Qantas, Qatar Airways, Singapore KrisFlyer, Turkish Airlines, United, Virgin Atlantic.
Not covered (absent across ~20 test routes) — Aer Lingus AerClub, Avianca LifeMiles,
Cathay Pacific Asia Miles, EVA Air, Malaysia Airlines, TAP Air Portugal. Also flight-
only: hotel pools (Hyatt, Marriott, IHG, Accor, Choice, Wyndham) have no equivalent
here. Unmapped/uncovered results still show under their raw seats.aero source name
rather than being hidden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

SEARCH_URL = "https://seats.aero/partnerapi/search"

_CABIN_PREFIX = {"ECONOMY": "Y", "PREMIUM_ECONOMY": "W", "BUSINESS": "J", "FIRST": "F"}

# seats.aero Source slug -> cards_data.py Partner.name, confirmed by live query 2026-07-22.
SOURCE_TO_PARTNER = {
    "aeroplan": "Air Canada Aeroplan",
    "aeromexico": "Aeromexico Rewards",
    "british": "British Airways Executive Club",
    "etihad": "Etihad Guest",
    "finnair": "Finnair Plus",
    "flyingblue": "Air France-KLM Flying Blue",
    "iberia": "Iberia Plus",
    "jetblue": "JetBlue TrueBlue",
    "qantas": "Qantas Frequent Flyer",
    "qatar": "Qatar Airways Privilege Club",
    "singapore": "Singapore KrisFlyer",
    "turkish": "Turkish Airlines Miles&Smiles",
    "united": "United MileagePlus",
    "virginatlantic": "Virgin Atlantic Flying Club",
}


class NotConfigured(Exception):
    """Raised when a seats.aero API key isn't set."""


class SearchFailed(Exception):
    """Raised when the live search fails. Message is safe to display — it never
    contains the request URL or API key."""


@dataclass
class AwardOffer:
    source: str  # raw seats.aero source slug, e.g. "flyingblue"
    cabin: str  # "ECONOMY" | "PREMIUM_ECONOMY" | "BUSINESS" | "FIRST"
    date: str  # "YYYY-MM-DD"
    points: int
    taxes_fees: float
    taxes_currency: str
    remaining_seats: int
    airlines: str
    direct: bool
    origin: str
    destination: str

    @property
    def program(self) -> str:
        return SOURCE_TO_PARTNER.get(self.source, self.source.replace("_", " ").title())

    @property
    def known_partner(self) -> bool:
        return self.source in SOURCE_TO_PARTNER


def _get_api_key() -> str:
    key = os.environ.get("SEATS_AERO_API_KEY")
    if not key:
        try:
            import streamlit as st

            key = st.secrets.get("SEATS_AERO_API_KEY")
        except Exception:
            pass
    if not key:
        raise NotConfigured(
            "Not configured. Subscribe at https://seats.aero to get a developer API key, "
            "then set SEATS_AERO_API_KEY as an environment variable or in "
            ".streamlit/secrets.toml and restart the app."
        )
    return key


def is_configured() -> bool:
    try:
        _get_api_key()
        return True
    except NotConfigured:
        return False


def search_award_availability(
    origin: str,
    destination: str,
    start_date: str,
    end_date: str | None = None,
    cabin: str = "BUSINESS",
    max_results: int = 10,
) -> list[AwardOffer]:
    """
    Query cached award availability for a route/date-range/cabin, cheapest points first.

    Raises NotConfigured if no API key is set, or SearchFailed if the seats.aero call
    itself fails (network error, daily quota exhausted, bad response).
    """
    api_key = _get_api_key()
    prefix = _CABIN_PREFIX.get(cabin.upper(), "J")
    try:
        resp = requests.get(
            SEARCH_URL,
            headers={"Partner-Authorization": api_key},
            params={
                "origin_airport": origin.upper(),
                "destination_airport": destination.upper(),
                "start_date": start_date,
                "end_date": end_date or start_date,
                "take": 1000,
            },
            timeout=20,
        )
        if resp.status_code == 429:
            raise SearchFailed("Daily seats.aero quota (1,000 calls) is used up. Try again after it resets.")
        resp.raise_for_status()
        payload = resp.json()
    except requests.HTTPError:
        raise SearchFailed(f"seats.aero returned HTTP {resp.status_code}. Check your key at seats.aero.")
    except requests.RequestException as e:
        raise SearchFailed(f"Network error during search: {type(e).__name__}. Try again.")
    except ValueError:
        raise SearchFailed("seats.aero returned an unreadable response. Try again.")

    offers = []
    for item in payload.get("data", []):
        if not item.get(f"{prefix}Available"):
            continue
        route = item.get("Route", {})
        offers.append(
            AwardOffer(
                source=item.get("Source", "unknown"),
                cabin=cabin.upper(),
                date=item.get("Date", start_date),
                points=int(item.get(f"{prefix}MileageCostRaw", 0)),
                taxes_fees=float(item.get(f"{prefix}TotalTaxesRaw", 0)),
                taxes_currency=item.get("TaxesCurrency", "USD"),
                remaining_seats=int(item.get(f"{prefix}RemainingSeatsRaw", 0)),
                airlines=item.get(f"{prefix}Airlines", ""),
                direct=bool(item.get(f"{prefix}DirectRaw", False)),
                origin=route.get("OriginAirport", origin.upper()),
                destination=route.get("DestinationAirport", destination.upper()),
            )
        )
    offers.sort(key=lambda o: o.points)
    return offers[:max_results]
