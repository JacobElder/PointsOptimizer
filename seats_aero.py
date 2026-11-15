"""
seats.aero Partner API: the key, the Cached Search URL and program names.

Pro subscription required: https://seats.aero -- self-serve, includes a developer API
key good for 1,000 calls/day (tracked via the x-ratelimit-* response headers, not a
per-minute throttle). The searches themselves live in award_scanner.py (Cached
Search) and award_trips.py (Get Trips). A one-route search function here
(search_award_availability) had no callers left and was removed 2026-11-15.

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

SEARCH_URL = "https://seats.aero/partnerapi/search"

# seats.aero Source slug -> cards_data.py Partner.name, confirmed by live query 2026-07-22.
SOURCE_TO_PARTNER = {
    "aeroplan": "Air Canada Aeroplan",
    "aeromexico": "Aeromexico Rewards",
    "alaska": "Alaska Atmos Rewards",  # added 2026-09-16 (live-verified)
    "american": "American Airlines AAdvantage",  # added 2026-09-16 (live-verified)
    "emirates": "Emirates Skywards",  # added 2026-09-16 (live-verified)
    "lifemiles": "Avianca LifeMiles",  # added 2026-09-23
    "smiles": "GOL Smiles",  # added 2026-09-23
    "british": "British Airways Executive Club",
    "delta": "Delta SkyMiles",  # added 2026-09-16 (live-verified); not a transfer partner of your pools
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
