"""
Scans seats.aero's Cached Search directly for award space on your routes,
restricted to loyalty programs your *active* point pools can transfer to.

Replaces the Gmail alert-email firehose: instead of one email per matching
flight, one scan pulls everything, and deal_finder.py ranks it.

API facts (verified 2026-09-16): Pro = 1,000 calls/day; Cached Search takes
comma-separated origin/destination airports plus `cabins` and `sources`
filters, pages up to 1,000 rows via skip + cursor, and returns Route.Distance.
TotalTaxes are in minor units (cents). A full scan is one query per program per
cabin, ~60-100 calls.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import requests

import seats_aero
from cards_data import POOLS, pool_is_active

_BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_BASE, "scan_config.json")

_CABIN_PREFIX = {"ECONOMY": "Y", "PREMIUM_ECONOMY": "W", "BUSINESS": "J", "FIRST": "F"}
_CABIN_PARAM = {"ECONOMY": "economy", "PREMIUM_ECONOMY": "premium", "BUSINESS": "business", "FIRST": "first"}
_PARTNER_TO_SOURCE = {v: k for k, v in seats_aero.SOURCE_TO_PARTNER.items()}
MAX_PAGES_PER_CABIN = 25  # per program and cabin; hitting it is logged as truncated


@dataclass
class AwardCandidate:
    id: str
    source: str
    program: str
    origin: str
    dest: str
    date: str
    cabin: str
    points: int
    taxes: float  # in taxes_currency major units
    taxes_currency: str
    seats: int
    direct: bool
    airlines: str
    distance: int
    updated_at: str

    def as_dict(self) -> dict:
        return asdict(self)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def transferable_sources(include_planned: bool = False) -> dict[str, list[str]]:
    """seats.aero source slug -> pool keys that can fund it (active pools only by default)."""
    out: dict[str, list[str]] = {}
    for key, pool in POOLS.items():
        if not pool.transferable or not (include_planned or pool_is_active(key)):
            continue
        for partner in pool.partners:
            src = _PARTNER_TO_SOURCE.get(partner.name)
            if src:
                out.setdefault(src, []).append(key)
    return out


def _parse(item: dict, cabin: str) -> AwardCandidate | None:
    p = _CABIN_PREFIX[cabin]
    if not item.get(f"{p}Available"):
        return None
    try:
        points = int(item.get(f"{p}MileageCostRaw") or item.get(f"{p}MileageCost") or 0)
    except (TypeError, ValueError):
        return None
    if points <= 0:
        return None
    route = item.get("Route", {})
    source = item.get("Source", "")
    return AwardCandidate(
        id=item.get("ID", ""),
        source=source,
        program=seats_aero.SOURCE_TO_PARTNER.get(source, source),
        origin=route.get("OriginAirport", ""),
        dest=route.get("DestinationAirport", ""),
        date=item.get("Date", ""),
        cabin=cabin,
        points=points,
        taxes=float(item.get(f"{p}TotalTaxesRaw") or 0) / 100.0,
        taxes_currency=item.get("TaxesCurrency") or "USD",
        seats=int(item.get(f"{p}RemainingSeatsRaw") or 0),
        direct=bool(item.get(f"{p}DirectRaw")),
        airlines=item.get(f"{p}AirlinesRaw") or item.get(f"{p}Airlines") or "",
        distance=int(route.get("Distance") or 0),
        updated_at=item.get("UpdatedAt", ""),
    )


def sources_for_pool(pool_key: str) -> list[str]:
    """seats.aero slugs for a pool's airline partners (whether or not the pool is active)."""
    return [_PARTNER_TO_SOURCE[pt.name] for pt in POOLS[pool_key].partners if pt.name in _PARTNER_TO_SOURCE]


def sources_for_programs(program_names) -> list[str]:
    """seats.aero slugs for programs (cards_data Partner names) you hold miles in."""
    return [_PARTNER_TO_SOURCE[n] for n in program_names if n in _PARTNER_TO_SOURCE]


def scan(config: dict | None = None, include_planned: bool = False, today: date | None = None,
         session: requests.Session | None = None,
         extra_sources: list[str] | None = None, per_source: bool = True) -> tuple[list[AwardCandidate], dict]:
    """Run one scan. Returns (candidates, stats).

    extra_sources: programs to scan beyond what your pools can transfer to
    (e.g. ones you already hold miles in). per_source=False sends one query for
    all programs per cabin: fine for a single route, but a broad multi-destination
    scan needs per_source=True to stay under the page cap. Config may set
    start_date/end_date (YYYY-MM-DD) instead of min/max_days_out.
    """
    cfg = config or load_config()
    today = today or datetime.now(timezone.utc).date()
    sources = list(transferable_sources(include_planned))
    sources += [s for s in (extra_sources or []) if s not in sources]
    wanted_sources = [s for s in sources if not cfg.get("sources") or s in cfg["sources"]]
    start = (date.fromisoformat(cfg["start_date"]) if cfg.get("start_date")
             else today + timedelta(days=int(cfg.get("min_days_out", 3))))
    end = (date.fromisoformat(cfg["end_date"]) if cfg.get("end_date")
           else today + timedelta(days=int(cfg.get("max_days_out", 330))))
    max_age = timedelta(days=int(cfg.get("max_data_age_days", 10)))
    now = datetime.now(timezone.utc)
    http = session or requests.Session()
    key = seats_aero._get_api_key()

    stats = {"calls": 0, "rows": 0, "stale": 0, "sources": wanted_sources, "rate_limit_remaining": None,
             "truncated": []}
    found: dict[tuple, AwardCandidate] = {}
    # One query per cabin PER PROGRAM: a combined query across ~15 programs and ~100
    # destinations returned >25k economy rows and silently cut off whole programs
    # (American's 9,500-mile Caribbean awards were never seen).
    groups = [[src] for src in wanted_sources] if per_source else [wanted_sources]
    for cabin, group in [(c, g) for c in cfg["cabins"] for g in groups]:
        source = ",".join(group)
        params = {
            "origin_airport": ",".join(cfg["origins"]),
            "destination_airport": ",".join(cfg["destinations"]),
            "cabins": _CABIN_PARAM[cabin],
            "sources": source,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "take": 1000,
            "order_by": "lowest_mileage",
        }
        skip, cursor = 0, None
        for page_no in range(MAX_PAGES_PER_CABIN):
            page = dict(params, skip=skip, **({"cursor": cursor} if cursor else {}))
            resp = http.get(seats_aero.SEARCH_URL, headers={"Partner-Authorization": key}, params=page, timeout=90)
            stats["calls"] += 1
            stats["rate_limit_remaining"] = resp.headers.get("x-ratelimit-remaining")
            if resp.status_code == 429:
                raise seats_aero.SearchFailed("seats.aero daily quota (1,000 calls) is used up.")
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data", [])
            stats["rows"] += len(rows)
            for item in rows:
                c = _parse(item, cabin)
                if c is None:
                    continue
                try:
                    updated = datetime.fromisoformat(c.updated_at.replace("Z", "+00:00"))
                    if now - updated > max_age:
                        stats["stale"] += 1
                        continue
                except ValueError:
                    pass
                k = (c.source, c.origin, c.dest, c.date, c.cabin)
                if k not in found or c.points < found[k].points:
                    found[k] = c
            if not payload.get("hasMore") or not rows:
                break
            if page_no == MAX_PAGES_PER_CABIN - 1:
                stats["truncated"].append(f"{source}/{cabin}")
            skip += len(rows)
            cursor = payload.get("cursor", cursor)
    stats["candidates"] = len(found)
    return list(found.values()), stats
