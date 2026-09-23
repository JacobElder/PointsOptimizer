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

PROGRAM_VALUES_PATH = os.path.join(_BASE, "program_values.json")

# A deal is a standout only if it beats what those points are normally worth by
# this much. A flat cabin bar rewarded programs whose points are simply worth
# more (a routine 88k United award cleared a 2.0c "business" bar), so the bar is
# now per program: baseline x GREAT_MULTIPLE.
# How far above the baseline a deal must be to be called a standout. Premium
# cabins use a smaller multiple because their baseline already embeds the premium
# uplift; multiplying both would demand ~3.4c from Aeroplan business, stricter
# than a genuinely good business redemption priced the conservative way this app
# prices them (cheapest comparable fare, or half a round trip).
GREAT_MULTIPLE = {"ECONOMY": 1.6, "PREMIUM_ECONOMY": 1.5, "BUSINESS": 1.25, "FIRST": 1.25}
DEFAULT_GREAT_MULTIPLE = 1.6
# Floors so a low-value program can't set a trivially easy bar.
MIN_GREAT_CPP = {"ECONOMY": 1.5, "PREMIUM_ECONOMY": 1.5, "BUSINESS": 1.8, "FIRST": 1.8}
SKIP_CPP = 1.0  # never call anything below this a mere "borderline"

_program_values: dict | None = None


def program_values() -> dict:
    global _program_values
    if _program_values is None:
        try:
            with open(PROGRAM_VALUES_PATH) as f:
                _program_values = json.load(f)
        except (OSError, json.JSONDecodeError):
            _program_values = {"values": {}, "default_cpp": 1.3}
    return _program_values


_PREMIUM_CABINS = {"BUSINESS", "FIRST"}


def baseline_cpp(program: str, cabin: str = "") -> float:
    """What one point in this program is typically worth, in cents.

    Premium-cabin redemptions genuinely return more per point, so a single number
    made business bars too easy: published business values are used where they
    exist, else economy x business_multiplier.
    """
    data = program_values()
    economy = float(data["values"].get(program, data.get("default_cpp", 1.3)))
    if (cabin or "").upper() not in _PREMIUM_CABINS:
        return economy
    business = data.get("business_values", {}).get(program)
    return float(business) if business else economy * float(data.get("business_multiplier", 1.4))

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


def great_floor(cabin: str, program: str = "") -> float:
    """The CPP bar at/above which a deal is a standout for this program and cabin."""
    multiple = GREAT_MULTIPLE.get((cabin or "").upper(), DEFAULT_GREAT_MULTIPLE)
    return max(baseline_cpp(program, cabin) * multiple,
               MIN_GREAT_CPP.get((cabin or "").upper(), 1.8))


def verdict_for(cpp: float | None, cabin: str, program: str = "") -> str:
    """BOOK beats the program's typical value by GREAT_MULTIPLE; SKIP is worth
    less than simply using those points normally."""
    if cpp is None:
        return "NO CASH PRICE"
    if cpp >= great_floor(cabin, program):
        return "BOOK"
    if cpp < max(baseline_cpp(program, cabin), SKIP_CPP):
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
