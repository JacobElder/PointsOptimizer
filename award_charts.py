"""
Curated award-chart sweet spots for programs reachable from the user's pools.

Every entry was verified against live sources on the last_verified date — award
charts churn constantly (Aeroplan devalued June 2026), so treat stale dates with
suspicion and re-verify before booking.

pricing_model values:
  chart          — published fixed chart; number is reliable until devalued
  zone           — zone/distance chart; number is the relevant band's price
  dynamic-floor  — dynamic pricing with a maintained floor; number is the floor
  dynamic        — fully dynamic; number is a typical observed price, not a promise
"""

from dataclasses import dataclass


@dataclass
class SweetSpot:
    program: str  # must match Partner.name in cards_data.py for lookup
    route: str
    cabin: str
    points_one_way: int
    pricing_model: str
    last_verified: str  # ISO date
    notes: str = ""


SWEET_SPOTS: list[SweetSpot] = [
    # ── Virgin Atlantic Flying Club (Chase UR 1:1, WF 1:1) ─────────────────
    SweetSpot(
        "Virgin Atlantic Flying Club", "US West Coast → Japan (ANA)", "First (The Suite)",
        60000, "chart", "2026-07-14",
        "One of the best redemptions anywhere: ~$10k+ cash seats. 55k from some cities. "
        "Book via Virgin as ANA partner award; minimal surcharges.",
    ),
    SweetSpot(
        "Virgin Atlantic Flying Club", "US West Coast → Japan (ANA)", "Business",
        45000, "chart", "2026-07-14",
        "East Coast 47,500; Hawaii → Tokyo just 35,000.",
    ),
    SweetSpot(
        "Virgin Atlantic Flying Club", "US → Europe (Delta One)", "Business",
        50000, "chart", "2026-07-14",
        "Often far cheaper than Delta SkyMiles for the same seat.",
    ),
    SweetSpot(
        "Virgin Atlantic Flying Club", "Delta short-haul (<500 mi)", "Economy",
        7500, "chart", "2026-07-14",
        "Under-1,000-mi nonstops 8,500 (e.g. ATL→Nassau).",
    ),
    # ── Air France-KLM Flying Blue (Chase UR 1:1, WF 1:1, Citi 1:1) ────────
    SweetSpot(
        "Air France-KLM Flying Blue", "North America → Europe", "Economy",
        25000, "dynamic-floor", "2026-07-14",
        "Floor price; Promo Rewards (monthly, 25-50% off) drop this to 18,750. "
        "Check promos on the 1st of each month.",
    ),
    SweetSpot(
        "Air France-KLM Flying Blue", "North America → Europe", "Business",
        55000, "dynamic-floor", "2026-07-14",
        "Floor ~55-60k; July 2026 Promo Rewards had 45k from a dozen NA cities.",
    ),
    SweetSpot(
        "Air France-KLM Flying Blue", "North America → Europe", "Premium Economy",
        40000, "dynamic-floor", "2026-07-14",
        "30k on Promo Rewards routes.",
    ),
    # ── Air Canada Aeroplan (Chase UR 1:1) ──────────────────────────────────
    SweetSpot(
        "Air Canada Aeroplan", "East Coast NA → Europe (≤4,000 mi)", "Economy",
        32500, "zone", "2026-07-14",
        "Post-June-2026 chart (this one got CHEAPER in the devaluation). "
        "NYC/BOS to Western Europe fits the band.",
    ),
    SweetSpot(
        "Air Canada Aeroplan", "East Coast NA → Europe (≤4,000 mi)", "Business",
        60000, "zone", "2026-07-14",
        "Longer transatlantic (4,001-6,000 mi, most of US → Europe) rose to 75k in the "
        "June 2026 devaluation.",
    ),
    # ── Avios: BA / Iberia / Aer Lingus (Chase UR 1:1, WF 1:1) ─────────────
    SweetSpot(
        "British Airways Executive Club", "Short-haul partner flights (Zone 1, ≤650 mi)", "Economy",
        4000, "zone", "2026-07-14",
        "Aer Lingus Zone 1 from 4,000 Avios; Iberia 5,000. BA no longer publishes an "
        "official chart — treat as observed band pricing. Avios move 1:1 between "
        "BA/Iberia/Aer Lingus, so book through whichever prices lowest.",
    ),
    SweetSpot(
        "British Airways Executive Club", "US East Coast → Europe", "Economy",
        26000, "zone", "2026-07-14",
        "Off-peak band pricing ~25-26k; watch BA's fuel surcharges on BA metal — "
        "Aer Lingus/Iberia metal usually carries lower fees for the same Avios.",
    ),
]


def spots_for_program(program_name: str) -> list[SweetSpot]:
    q = program_name.strip().lower()
    if not q:
        return []
    return [s for s in SWEET_SPOTS if q in s.program.lower() or s.program.lower() in q]
