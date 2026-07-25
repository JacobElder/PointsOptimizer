"""
Resolve a user-typed origin/destination into an IATA code the search APIs accept.

Both seats.aero (Cached Search) and SerpApi (Google Flights) accept 3-letter IATA
codes -- and, verified against the live seats.aero API 2026-07-25, metropolitan-area
codes too (e.g. NYC covers JFK/EWR/LGA, LON covers LHR/LGW/etc.). So a city search
resolves to the metro code where one exists (broadest coverage), otherwise to the
city's primary airport.

resolve() accepts either a raw 3-letter code (passed straight through) or a city
name / common alias (case-insensitive), and returns (code, label) or (None, None).
"""

from __future__ import annotations

# City / alias (lowercased) -> IATA code. Metro codes preferred over single airports.
_CITY_TO_CODE = {
    # North America (metro codes cover all their airports)
    "new york": "NYC", "nyc": "NYC", "new york city": "NYC", "manhattan": "NYC",
    "washington": "WAS", "washington dc": "WAS", "dc": "WAS",
    "chicago": "CHI",
    "los angeles": "LAX", "la": "LAX",
    "san francisco": "SFO", "sf": "SFO", "bay area": "SFO",
    "toronto": "YTO", "montreal": "YMQ", "vancouver": "YVR",
    "boston": "BOS", "miami": "MIA", "atlanta": "ATL", "dallas": "DFW",
    "houston": "IAH", "seattle": "SEA", "denver": "DEN", "las vegas": "LAS",
    "orlando": "MCO", "philadelphia": "PHL", "phoenix": "PHX", "newark": "EWR",
    "mexico city": "MEX", "cancun": "CUN",
    # South America
    "buenos aires": "BUE", "sao paulo": "SAO", "são paulo": "SAO",
    "rio de janeiro": "RIO", "rio": "RIO", "santiago": "SCL", "lima": "LIM",
    "bogota": "BOG", "bogotá": "BOG", "cartagena": "CTG", "panama city": "PTY",
    "san jose": "SJO", "liberia": "LIR", "montego bay": "MBJ",
    # Europe
    "london": "LON", "paris": "PAR", "milan": "MIL", "rome": "ROM",
    "moscow": "MOW", "stockholm": "STO",
    "madrid": "MAD", "barcelona": "BCN", "lisbon": "LIS", "porto": "OPO",
    "amsterdam": "AMS", "frankfurt": "FRA", "munich": "MUC", "berlin": "BER",
    "zurich": "ZRH", "geneva": "GVA", "vienna": "VIE", "brussels": "BRU",
    "copenhagen": "CPH", "oslo": "OSL", "helsinki": "HEL", "dublin": "DUB",
    "athens": "ATH", "istanbul": "IST", "prague": "PRG", "budapest": "BUD",
    "warsaw": "WAW", "reykjavik": "KEF", "nice": "NCE", "venice": "VCE",
    "naples": "NAP", "edinburgh": "EDI", "manchester": "MAN",
    # Middle East / Africa
    "dubai": "DXB", "abu dhabi": "AUH", "doha": "DOH", "tel aviv": "TLV",
    "cairo": "CAI", "casablanca": "CMN", "johannesburg": "JNB",
    "cape town": "CPT", "nairobi": "NBO", "addis ababa": "ADD",
    # Asia / Pacific
    "tokyo": "TYO", "osaka": "OSA", "seoul": "SEL", "beijing": "BJS",
    "shanghai": "SHA",
    "hong kong": "HKG", "singapore": "SIN", "bangkok": "BKK", "bali": "DPS",
    "denpasar": "DPS", "kuala lumpur": "KUL", "jakarta": "CGK", "manila": "MNL",
    "taipei": "TPE", "delhi": "DEL", "mumbai": "BOM", "bangalore": "BLR",
    "sydney": "SYD", "melbourne": "MEL", "auckland": "AKL",
    "ho chi minh city": "SGN", "hanoi": "HAN",
}


def resolve(query: str) -> tuple[str | None, str | None]:
    """Return (IATA code, human label) for a code or city name, or (None, None)."""
    if not query:
        return None, None
    q = query.strip()
    # A bare 3-letter token is already a code (airport or metro) -- pass it through.
    if len(q) == 3 and q.isalpha():
        return q.upper(), q.upper()
    code = _CITY_TO_CODE.get(q.lower())
    if code:
        return code, f"{q.strip().title()} ({code})"
    return None, None


def is_resolvable(query: str) -> bool:
    return resolve(query)[0] is not None


def options() -> list[str]:
    """Sorted "City (CODE)" labels for a searchable dropdown, deduped by code.

    The first alias listed for each code in _CITY_TO_CODE is its canonical name.
    """
    canonical: dict[str, str] = {}
    for name, code in _CITY_TO_CODE.items():
        canonical.setdefault(code, f"{name.title()} ({code})")
    return sorted(canonical.values())


def code_from_option(option: str | None) -> str | None:
    """"New York (NYC)" -> "NYC"; tolerant of a raw code or None."""
    if not option:
        return None
    if option.endswith(")") and "(" in option:
        return option.rsplit("(", 1)[-1].rstrip(")").strip().upper()
    return option.strip().upper() or None


def option_from_code(code: str | None) -> str | None:
    """"SFO" -> "San Francisco (SFO)" if it's in the dropdown, else None."""
    if not code:
        return None
    code = code.strip().upper()
    for opt in options():
        if code_from_option(opt) == code:
            return opt
    return None


# The canonical dropdown label for New York, used as the default origin.
DEFAULT_ORIGIN_OPTION = "New York (NYC)"
