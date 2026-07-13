"""
Optional live cash-price lookups via SerpApi's Google Flights engine.

Free tier: https://serpapi.com — self-serve signup, 250 searches/month free,
no sales call required. Only used to auto-fill the *cash* price side of a CPP
calculation; award point-costs still have to be entered manually.

The Flight Analyzer page falls back to manual entry if this isn't configured.
Configure via environment variable or Streamlit secrets:
    SERPAPI_KEY
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

SEARCH_URL = "https://serpapi.com/search"

_TRAVEL_CLASS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}


class NotConfigured(Exception):
    """Raised when a SerpApi key isn't set."""


class SearchFailed(Exception):
    """Raised when the live search fails. Message is safe to display — it never
    contains the request URL, which embeds the API key as a query parameter."""


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


def is_configured() -> bool:
    try:
        _get_api_key()
        return True
    except NotConfigured:
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

    Raises NotConfigured if no API key is set, or SearchFailed if the SerpApi
    call itself fails (network error, rate limit, bad response).
    """
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

        offers.append(
            FlightOffer(
                price_usd=float(item["price"]),
                cabin=cabin.upper(),
                total_duration_minutes=item.get("total_duration", sum(s.duration_minutes for s in segments)),
                segments=segments,
                layovers=layovers,
            )
        )
    offers.sort(key=lambda o: o.price_usd)
    return offers[:max_results]
