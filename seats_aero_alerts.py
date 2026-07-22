"""
Parser for seats.aero "flights found for your alert" email notifications.

seats.aero's Partner API has no endpoint for "give me my saved alerts" — alerts
are a mailbox feature, not an API resource. So the path here is: fetch the
alert emails yourself (Gmail, mbox export, whatever), pass each raw HTML body
to parse_alert_email(), and this pulls out exactly the fields needed to run a
CPP check — origin, destination, date, cabin, program, points, and the award's
taxes/fees. It deliberately leaves cash price blank: that side still comes from
flight_search.search_cash_price(), same as everywhere else in this app.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass


@dataclass
class ParsedAlert:
    alert_name: str | None = None
    program: str | None = None
    cabin: str | None = None  # "ECONOMY" | "PREMIUM_ECONOMY" | "BUSINESS" | "FIRST"
    fare_class: str | None = None  # single-letter booking class, e.g. "O9"
    origin_iata: str | None = None
    destination_iata: str | None = None
    date: str | None = None  # "YYYY-MM-DD"
    flight_number: str | None = None
    points: int | None = None
    taxes_fees: float | None = None
    taxes_currency: str = "USD"

    @property
    def found_anything(self) -> bool:
        return any([self.program, self.points, self.origin_iata])


_CABIN_WORDS = {
    "economy": "ECONOMY",
    "premium economy": "PREMIUM_ECONOMY",
    "premium": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}


def _to_text(raw: str) -> str:
    """Strip HTML tags/entities down to plain, whitespace-collapsed text."""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_alert_email(raw_html_or_text: str) -> ParsedAlert:
    alert = ParsedAlert()
    if not raw_html_or_text or not raw_html_or_text.strip():
        return alert

    text = _to_text(raw_html_or_text)

    name_match = re.search(r'alert "([^"]+)"', text)
    if name_match:
        alert.alert_name = name_match.group(1)

    # "...with Air France/KLM Flying Blue in business class for JFK to MAD on 2026-11-04."
    main_match = re.search(
        r"with (.+?) in (\w[\w ]*?) class for ([A-Z]{3}) to ([A-Z]{3}) on (\d{4}-\d{2}-\d{2})",
        text,
    )
    if main_match:
        alert.program = main_match.group(1).strip()
        alert.cabin = _CABIN_WORDS.get(main_match.group(2).strip().lower())
        alert.origin_iata = main_match.group(3)
        alert.destination_iata = main_match.group(4)
        alert.date = main_match.group(5)

    # "UX92 Flight JFK/MAD Routing Business (O9), 43,000 points + $33.50 USD Fare"
    # Connections list multiple comma-separated flight numbers, e.g. "CM815, CM304".
    fare_match = re.search(
        r"(\w{2}\d{1,5}(?:,\s*\w{2}\d{1,5})*)\s*Flight\s*([A-Z]{3})(?:/[A-Z]{3})*/([A-Z]{3})\s*Routing\s*"
        r"(\w[\w ]*?)\s*\(([A-Z0-9]+)\),\s*([\d,]+)\s*points\s*\+\s*\$([\d,.]+)\s*([A-Z]{3})\s*Fare",
        text,
    )
    if fare_match:
        alert.flight_number = fare_match.group(1)
        alert.origin_iata = alert.origin_iata or fare_match.group(2)
        alert.destination_iata = alert.destination_iata or fare_match.group(3)
        alert.cabin = alert.cabin or _CABIN_WORDS.get(fare_match.group(4).strip().lower())
        alert.fare_class = fare_match.group(5)
        alert.points = int(fare_match.group(6).replace(",", ""))
        alert.taxes_fees = float(fare_match.group(7).replace(",", ""))
        alert.taxes_currency = fare_match.group(8)

    return alert
