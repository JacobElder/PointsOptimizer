"""
Best-effort parser for pasted Going.com deal-alert text.

Going has no API, so the lightweight path is: paste the alert email body, pull
out what's parseable (price, route, airports, dates), and pre-fill the Flight
Analyzer. Parsing is deliberately forgiving — any field it can't find is just
left blank for manual entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedDeal:
    price_usd: float | None = None
    origin_iata: str | None = None
    destination_iata: str | None = None
    route_text: str | None = None  # e.g. "San Francisco to Tokyo"
    is_roundtrip: bool = False
    raw_dates: list[str] = field(default_factory=list)

    @property
    def found_anything(self) -> bool:
        return any([self.price_usd, self.route_text, self.origin_iata])


# Words that commonly appear in ALL CAPS in deal emails but aren't airports.
_IATA_STOPWORDS = {
    "THE", "AND", "FOR", "NEW", "NOW", "GET", "OFF", "USD", "PST", "EST",
    "FAQ", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT", "JAN", "FEB",
    "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
}


def parse_deal(text: str) -> ParsedDeal:
    deal = ParsedDeal()
    if not text or not text.strip():
        return deal

    # Price: first $ amount (Going quotes roundtrip totals like "$650 roundtrip")
    price_match = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
    if price_match:
        deal.price_usd = float(price_match.group(1).replace(",", ""))

    deal.is_roundtrip = bool(re.search(r"round[\s-]?trip", text, re.IGNORECASE))

    # Route: "City A to City B" phrasing (word chars, spaces, dots for "St. Louis")
    route_match = re.search(
        r"([A-Z][\w.'\s]{2,30}?)\s+to\s+([A-Z][\w.'\s]{2,30}?)(?=[\s,.:;!\n]|$)",
        text,
    )
    if route_match:
        deal.route_text = f"{route_match.group(1).strip()} to {route_match.group(2).strip()}"

    # IATA codes: standalone 3-letter uppercase tokens, or "SFO-NRT" pairs
    pair = re.search(r"\b([A-Z]{3})\s*[-–→]\s*([A-Z]{3})\b", text)
    if pair and pair.group(1) not in _IATA_STOPWORDS and pair.group(2) not in _IATA_STOPWORDS:
        deal.origin_iata, deal.destination_iata = pair.group(1), pair.group(2)
    else:
        codes = [
            c for c in re.findall(r"\b([A-Z]{3})\b", text) if c not in _IATA_STOPWORDS
        ]
        if len(codes) >= 2:
            deal.origin_iata, deal.destination_iata = codes[0], codes[1]

    # Travel window: month-name date ranges ("August 2026", "Sep 3 - Nov 12")
    deal.raw_dates = re.findall(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:\s*[-–]\s*(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?\d{1,2})?(?:,?\s+\d{4})?",
        text,
    )

    return deal
