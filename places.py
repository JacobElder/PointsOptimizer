"""Human-readable names for airport and airline codes (OpenFlights data)."""

from __future__ import annotations

import json
import os
from datetime import datetime

_BASE = os.path.dirname(os.path.abspath(__file__))
_airports: dict | None = None
_airlines: dict | None = None

# OpenFlights city names that are wrong or unhelpful for a traveller.
_CITY_OVERRIDES = {
    "GCM": "Grand Cayman", "UVF": "Vieux Fort", "SJU": "San Juan", "STT": "St. Thomas",
    "SXM": "St. Maarten", "PLS": "Providenciales", "NAS": "Nassau", "AUA": "Aruba",
    "CUR": "Curaçao", "BGI": "Barbados", "ANU": "Antigua", "PUJ": "Punta Cana", "LIR": "Liberia",
    "HKT": "Phuket", "KIX": "Osaka", "NRT": "Tokyo", "HND": "Tokyo", "PVG": "Shanghai",
    "ICN": "Seoul", "CPT": "Cape Town", "JNB": "Johannesburg", "GRU": "São Paulo",
    "EZE": "Buenos Aires", "MDE": "Medellín", "CTA": "Catania, Sicily", "PMO": "Palermo, Sicily",
}
# Current, short names for carriers that show up in award results (OpenFlights has
# defunct or awkward names for several, e.g. AZ "Alitalia", 4Y "Airbus France").
_AIRLINE_OVERRIDES = {
    "4Y": "Discover Airlines", "4Z": "Airlink", "A3": "Aegean", "AA": "American", "AC": "Air Canada",
    "AF": "Air France", "AI": "Air India", "AM": "Aeromexico", "AS": "Alaska", "AT": "Royal Air Maroc",
    "AV": "Avianca", "AY": "Finnair", "AZ": "ITA Airways", "B6": "JetBlue", "BA": "British Airways",
    "BR": "EVA Air", "CI": "China Airlines", "CM": "Copa", "CX": "Cathay Pacific", "DE": "Condor",
    "DL": "Delta", "EI": "Aer Lingus", "EK": "Emirates", "EN": "Air Dolomiti", "ET": "Ethiopian",
    "EY": "Etihad", "FI": "Icelandair", "HA": "Hawaiian", "IB": "Iberia", "JL": "Japan Airlines",
    "KE": "Korean Air", "KL": "KLM", "KQ": "Kenya Airways", "LA": "LATAM", "LH": "Lufthansa",
    "LO": "LOT Polish", "LX": "Swiss", "MS": "EgyptAir", "NH": "ANA", "NZ": "Air New Zealand",
    "OS": "Austrian", "OZ": "Asiana", "QF": "Qantas", "QK": "Air Canada Express", "QR": "Qatar Airways",
    "SA": "South African", "SK": "SAS", "SN": "Brussels Airlines", "SQ": "Singapore Airlines",
    "TK": "Turkish Airlines", "TP": "TAP Air Portugal", "UA": "United", "UX": "Air Europa",
    "VL": "Lufthansa City", "VS": "Virgin Atlantic", "WK": "Edelweiss", "WN": "Southwest",
    "YU": "EuroAtlantic", "UX2": "Air Europa",
}
_METROS = {"NYC": "New York", "TYO": "Tokyo", "LON": "London", "PAR": "Paris", "MIL": "Milan",
           "ROM": "Rome", "CHI": "Chicago", "WAS": "Washington", "SEL": "Seoul", "OSA": "Osaka",
           "SAO": "São Paulo", "RIO": "Rio de Janeiro", "BUE": "Buenos Aires", "STO": "Stockholm",
           "BJS": "Beijing", "MOW": "Moscow", "YTO": "Toronto", "YMQ": "Montreal", "BER": "Berlin"}


def _load() -> None:
    global _airports, _airlines
    if _airports is None:
        with open(os.path.join(_BASE, "airport_coords.json")) as f:
            _airports = json.load(f)
        with open(os.path.join(_BASE, "airline_names.json")) as f:
            _airlines = json.load(f)


def city(code: str) -> str:
    _load()
    code = (code or "").upper()
    if code in _CITY_OVERRIDES:
        return _CITY_OVERRIDES[code]
    if code in _METROS:
        return _METROS[code]
    row = _airports.get(code)
    return row[3] if row and len(row) > 3 else code


def country(code: str) -> str:
    _load()
    row = _airports.get((code or "").upper())
    return row[4] if row and len(row) > 4 else ""


def airport_label(code: str, with_country: bool = True) -> str:
    """'GCM' -> 'Grand Cayman, Cayman Islands (GCM)'; US airports omit the country."""
    c = city(code)
    ctry = country(code)
    place = f"{c}, {ctry}" if with_country and ctry and ctry != "United States" and ctry not in c else c
    return f"{place} ({code.upper()})"


def airline_names(codes: str) -> str:
    """'CM, DL' -> 'Copa Airlines, Delta Air Lines'."""
    _load()
    out = []
    for raw in (codes or "").replace(";", ",").split(","):
        code = raw.strip().upper()
        if code:
            name = _AIRLINE_OVERRIDES.get(code) or _airlines.get(code) or code
            if name not in out:
                out.append(name)
    return ", ".join(out)


def md_safe(text: str) -> str:
    """Escape "$" so Streamlit markdown doesn't render text between two $ as LaTeX math."""
    return str(text).replace("$", "\\$")


def nice_date(iso: str, weekday: bool = True) -> str:
    """'2026-12-28' -> 'Mon, Dec 28, 2026'."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(iso)
    return d.strftime("%a, %b %-d, %Y" if weekday else "%b %-d")
