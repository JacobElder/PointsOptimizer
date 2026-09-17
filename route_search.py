"""
Best-CPP award search for one city pair, across a date range instead of one date.

Used by the Flight Analyzer's "Date range", "Any time" and "Outbound + return"
modes. Reuses the Deal Finder pipeline on a single route:
seats.aero scan (all your fundable programs, one query per cabin) -> fare-model
estimate -> free Google Flights prices for the most promising awards (one quote
covers +/-7 days) -> comparable-fare matching -> round-trip check on the leaders
-> rank by CPP.

Outbound + return is searched as TWO ONE-WAYS (usually better for points), then
the best pair with return after outbound is picked; its combined value is
checked against a real round-trip fare for those exact dates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import award_scanner
import cash_quotes
import deal_finder
import fare_model
import flight_search
import ledger
import valuation

DEFAULT_MAX_LOOKUPS = 30
ROUND_TRIP_CHECKS = 8


@dataclass
class RouteResult:
    deals: list[deal_finder.Scored]  # priced, best CPP first, one per program/origin/dest/cabin/points
    awards_found: int
    priced: int
    calls: int
    notes: list[str] = field(default_factory=list)


def _window(start: date | None, end: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    lo = max(start or today + timedelta(days=1), today + timedelta(days=1))
    hi = min(end or today + timedelta(days=cash_quotes.MAX_LOOKAHEAD_DAYS),
             today + timedelta(days=cash_quotes.MAX_LOOKAHEAD_DAYS))
    return lo, hi


def search(origin: str, dest: str, cabins: list[str], start: date | None = None, end: date | None = None,
           max_lookups: int = DEFAULT_MAX_LOOKUPS, top: int = 15, log=lambda m: None) -> RouteResult:
    """Best awards on origin -> dest (city or airport codes) within [start, end]."""
    lo, hi = _window(start, end)
    if hi < lo:
        return RouteResult([], 0, 0, 0, ["That date range is outside the bookable window (tomorrow to ~11 months out)."])
    program_balances = ledger.load_program_balances()
    transferable = award_scanner.transferable_sources()
    config = {"origins": [origin.upper()], "destinations": [dest.upper()], "cabins": cabins,
              "start_date": lo.isoformat(), "end_date": hi.isoformat(), "max_data_age_days": 10}
    log(f"Scanning seats.aero {origin}→{dest} {lo:%b %d} – {hi:%b %d, %Y}…")
    cands, stats = award_scanner.scan(config, extra_sources=award_scanner.sources_for_programs(program_balances),
                                      per_source=False)
    notes = []
    if stats.get("truncated"):
        notes.append("Very large result set; some awards may be missing.")
    model = fare_model.FareModel()
    scored = deal_finder.score_candidates(cands, model, [], program_balances)
    scored = [s for s in scored if s.c.source in transferable or s.bookable_now]
    log(f"Found {len(scored):,} awards you can fund. Pricing the most promising with Google Flights…")
    deal_finder.flight_search.SERPAPI_MAX_CALLS = 0  # interactive search never spends SerpApi quota
    # Rank for pricing by estimated CPP (not dollar surplus): this search is about the best rate.
    for s in scored:
        s.p_great = max(s.p_great, 0.2)  # price the best estimates even if below the usual bar
    ordered = sorted(scored, key=lambda s: -s.est_cpp)
    deal_finder.price_promising(ordered[: max_lookups * 20], max_lookups, log=lambda m: None)

    priced = [s for s in scored if s.cpp is not None]
    best: dict[tuple, deal_finder.Scored] = {}
    for s in sorted(priced, key=lambda s: -s.cpp):
        k = (s.c.source, s.c.origin, s.c.dest, s.c.cabin, s.c.points)
        if k not in best:
            best[k] = s
            s.other_dates, s.alternatives = [], []
        elif s.c.date != best[k].c.date:
            best[k].other_dates.append(s.c.date)
    deals = sorted(best.values(), key=lambda s: -s.cpp)
    rt_cache: dict = {}
    for s in deals[:ROUND_TRIP_CHECKS]:
        deal_finder.apply_round_trip(s, rt_cache)
    deals = sorted(deals, key=lambda s: -s.cpp)[:top]
    log("Getting flight details…")
    deal_finder.attach_trips(deals[:ROUND_TRIP_CHECKS + 4], {})
    # Mixed-cabin awards are worth less than their CPP suggests: list them after full-cabin ones.
    deals.sort(key=lambda s: (bool(s.trip and s.trip.mixed_cabin), -s.cpp))
    for s in deals:
        s.other_dates.sort()
    if not cands:
        notes.append("seats.aero has no award space for this route in your programs and dates.")
    return RouteResult(deals, len(scored), len(priced), stats["calls"], notes)


@dataclass
class PairResult:
    outbound: RouteResult
    inbound: RouteResult
    best_pair: tuple[deal_finder.Scored, deal_finder.Scored] | None
    combined_cpp: float | None = None
    pair_cash_one_ways: float | None = None
    pair_round_trip_fare: float | None = None


def search_pair(origin: str, dest: str, cabins: list[str], out_start: date, out_end: date,
                ret_start: date, ret_end: date, max_lookups: int = DEFAULT_MAX_LOOKUPS,
                log=lambda m: None) -> PairResult:
    """Outbound and return as two separate one-way awards; best compatible pair."""
    out = search(origin, dest, cabins, out_start, out_end, max_lookups, top=25, log=log)
    back = search(dest, origin, cabins, ret_start, ret_end, max_lookups, top=25, log=log)
    best_pair, best_value = None, None
    for o in out.deals:
        o_dates = [o.c.date] + o.other_dates
        for r in back.deals:
            r_dates = [r.c.date] + r.other_dates
            if not any(rd > od for od in o_dates for rd in r_dates):
                continue
            value = (o.cash + r.cash - o.taxes_usd - r.taxes_usd) / (o.c.points + r.c.points)
            if best_value is None or value > best_value:
                best_pair, best_value = (o, r), value
    result = PairResult(out, back, best_pair)
    if best_pair:
        o, r = best_pair
        od = o.c.date
        rd = min(d for d in [r.c.date] + r.other_dates if d > od)
        result.pair_cash_one_ways = o.cash + r.cash
        cash_total = result.pair_cash_one_ways
        try:
            offers = flight_search.search_round_trip_offers(o.c.origin, o.c.dest, od, rd,
                                                            deal_finder.price_cabin(o.c.cabin))
            if offers:
                result.pair_round_trip_fare = min(x.price_usd for x in offers)
                cash_total = min(cash_total, result.pair_round_trip_fare)
        except flight_search.SearchFailed:
            pass
        result.combined_cpp = valuation.compute_cpp(cash_total, o.taxes_usd + r.taxes_usd,
                                                       o.c.points + r.c.points)
    return result
