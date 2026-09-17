"""
CPP math, currency conversion, and the cabin-aware verdict: the single source
of truth used by the Deal Finder, route search and Flight Search.
"""

from __future__ import annotations

import json
import os

import requests

_BASE = os.path.dirname(os.path.abspath(__file__))
RECORDED_FARES_PATH = os.path.join(_BASE, "deal_log.json")

# "Great deal" bar: higher for Business/First, since committing a much larger
# points balance to one seat warrants more proof it's a standout. Premium Economy
# is bucketed with Economy. Confirmed with the user 2026-07-22.
GREAT_CPP_BY_CABIN = {"ECONOMY": 1.5, "PREMIUM_ECONOMY": 1.5, "BUSINESS": 2.0, "FIRST": 2.0}
SKIP_CPP = 1.0  # below this, a clear skip regardless of cabin

# Used only if the live rate lookup fails (offline, API down).
_FX_FALLBACK = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27}
_fx_cache: dict[str, float] = {}


def compute_cpp(cash_price: float, taxes_usd: float, points: int) -> float | None:
    """Cents per point: (cash fare - award taxes) / points * 100. None if points <= 0."""
    if points <= 0:
        return None
    return (max(cash_price - taxes_usd, 0.0) / points) * 100


def fx_rate(currency: str) -> float:
    """USD per 1 unit of currency, from a free no-key ECB-backed API, cached per process."""
    currency = (currency or "USD").upper()
    if currency == "USD":
        return 1.0
    if currency in _fx_cache:
        return _fx_cache[currency]
    try:
        resp = requests.get("https://api.frankfurter.app/latest", params={"from": currency, "to": "USD"}, timeout=5)
        resp.raise_for_status()
        rate = resp.json()["rates"]["USD"]
    except (requests.RequestException, KeyError, ValueError):
        rate = _FX_FALLBACK.get(currency, 1.0)
    _fx_cache[currency] = rate
    return rate


def great_floor(cabin: str) -> float:
    """The cabin-aware CPP bar at/above which a deal is a standout."""
    return GREAT_CPP_BY_CABIN.get((cabin or "").upper(), 2.0)


def verdict_for(cpp: float | None, cabin: str) -> str:
    if cpp is None:
        return "NO CASH PRICE"
    if cpp >= great_floor(cabin):
        return "BOOK"
    if cpp < SKIP_CPP:
        return "SKIP"
    return "BORDERLINE"


def recorded_fares() -> list[dict]:
    """Priced deals from the retired alert pipeline (deal_log.json): real Google
    Flights fares that still help train fare_model."""
    try:
        with open(RECORDED_FARES_PATH) as f:
            return json.load(f).get("deals", [])
    except (OSError, json.JSONDecodeError):
        return []
