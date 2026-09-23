"""
Deal finder: separates the wheat from the chaff in seats.aero award space.

Pipeline (one run, ~8 minutes, ~200-300 seats.aero calls of 1,000/day, 0 SerpApi):
  1. SCAN      award_scanner: every fresh award on scan_config.json's routes (plus
               watchlist destinations), in programs your active pools can transfer to.
  2. ESTIMATE  fare_model: estimated cash fare + uncertainty for every candidate,
               giving est. CPP and P(clears the bar). Watchlist matches use their
               own bar and get pricing priority.
  3. PRICE     cash_quotes (free Google Flights): real one-way fares for the most
               promising candidates. One quote also covers dates within +/-7 days.
  4. MATCH     cash_quotes.comparable_fare: nonstop award -> cheapest fare with at
               most 1 stop; otherwise cheapest fare. First class vs business fare.
  5. ROUND TRIP  for deals that make the report, also price a 7-night round trip;
               the award is valued against the LOWER of the one-way fare and half
               the round trip (one-way fares are often far above half a round trip).
  6. RANK      by dollars saved above the bar: (cash - taxes) - points * bar / 100.
               One entry per destination + cabin, other dates/origins/programs
               listed under it, 5 slots reserved for economy.
  7. REPORT    deal_digest.json (Deal Radar) + email of deals not reported in the
               last REPORT_COOLDOWN_DAYS: watchlist hits first, then the top list.

Usage:
    python deal_finder.py                 # scan, price, save digest, email new deals
    python deal_finder.py --no-email
    python deal_finder.py --include-planned   # preview cards you don't hold yet
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

import award_history
import award_scanner
import award_trips
import cash_quotes
import deal_email
import fare_model
import seats_aero
import flight_search
import ledger
import valuation

_BASE = os.path.dirname(os.path.abspath(__file__))
DIGEST_PATH = os.path.join(_BASE, "deal_digest.json")

MIN_P_GREAT_TO_PRICE = 0.2
MIN_P_WATCH_TO_PRICE = 0.05
WATCH_PRIORITY_BOOST = 5.0
DEFAULT_MAX_LOOKUPS = 150
DEFAULT_MAX_WATCH_LOOKUPS = 80  # separate budget so a long watchlist can't starve the general scan
DEFAULT_TOP = 20
REPORT_COOLDOWN_DAYS = 14
SERPAPI_CAP = 5
ROUND_TRIP_STAY_DAYS = 7
ROUND_TRIP_FALLBACK_STAYS = (7, 4, 2)  # shorter stays when the 7-night return is past the booking window
NO_ROUND_TRIP_PENALTY = 0.6  # a one-way-only valuation is usually too generous
ROUND_TRIP_CHECK_EXTRA = 15  # also round-trip-check this many runners-up, since leaders can drop out
WATCH_DEALS_PER_ENTRY = 2
HOLDOUT_LOOKUPS = 5  # deliberate duplicates near an existing quote, to measure reuse error
EXPLORE_SHARE = 0.25  # of the live budget, spent on route+cabin cells we haven't priced lately
EXPLORE_STALE_DAYS = 10
# How much a route's own price history may move a deal up or down the list.
HISTORY_WEIGHT = 0.3
FRESH_PRICE_SHORTLIST = True  # re-price reported deals on their own date before publishing
ESTIMATE_DRIFT_WARN_PCT = 45  # fare model is ~28% median error on unseen routes; well past that is a problem
MAX_PER_PROGRAM = 6  # one program's routine pricing shouldn't fill the whole list
WATCH_EMAIL_MAX = 10
HELD_MILES_DEALS = 5  # "book now with miles you already have" section size  # most valuable new watchlist hits per email; the rest are on Deal Radar


def price_cabin(cabin: str) -> str:
    """Cabin to compare cash fares against. Google's "first" search returns
    business or mixed-cabin itineraries on the many routes with no true first
    cabin, which would inflate CPP; first-class awards are valued against the
    business fare instead (conservative)."""
    return "BUSINESS" if cabin == "FIRST" else cabin


# ── watchlist ────────────────────────────────────────────────────────────────
@dataclass
class WatchEntry:
    label: str
    dests: list[str]
    origins: list[str] | None = None
    cabins: list[str] | None = None
    start: str | None = None
    end: str | None = None
    bar: float | None = None  # CPP bar; None = that program's usual standout bar

    @classmethod
    def from_config(cls, raw: dict) -> "WatchEntry":
        def up(xs):
            return [x.strip().upper() for x in xs] if xs else None
        return cls(label=raw.get("label") or ", ".join(raw["dests"]), dests=up(raw["dests"]),
                   origins=up(raw.get("origins")), cabins=up(raw.get("cabins")),
                   start=raw.get("start"), end=raw.get("end"),
                   bar=float(raw["bar"]) if raw.get("bar") is not None else None)

    def matches(self, c: award_scanner.AwardCandidate) -> bool:
        return (c.dest in self.dests
                and (not self.origins or c.origin in self.origins)
                and (not self.cabins or c.cabin in self.cabins)
                and (not self.start or c.date >= self.start)
                and (not self.end or c.date <= self.end))

    def bar_for(self, cabin: str, program: str = "") -> float:
        return self.bar if self.bar is not None else valuation.great_floor(cabin, program)


def load_watchlist(config: dict) -> list[WatchEntry]:
    return [WatchEntry.from_config(w) for w in config.get("watchlist", []) if w.get("dests")]


# ── scoring ──────────────────────────────────────────────────────────────────
@dataclass(eq=False, slots=True)  # one per scanned award: slots matter at ~150k
class Scored:
    c: award_scanner.AwardCandidate
    taxes_usd: float
    est: fare_model.Estimate
    floor: float
    est_cpp: float
    p_great: float
    watch: list[WatchEntry] = field(default_factory=list)
    p_watch: float = 0.0
    cash: float | None = None
    cash_approx: bool = False
    cash_basis: str = ""
    same_carrier_cash: float | None = None
    one_way_cash: float | None = None
    round_trip_half: float | None = None
    cpp: float | None = None
    surplus: float | None = None
    other_dates: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    held_miles: int = 0  # miles you already hold in this award's program
    trip: award_trips.TripInfo | None = None
    quote: cash_quotes.Quote | None = None
    rt_unavailable: bool = False  # couldn't price a round trip (date too far out)
    rt_stay_nights: int = ROUND_TRIP_STAY_DAYS
    trip_unverified: bool = False  # the live re-check failed; not proof the award is gone
    history_pct: float | None = None  # 1.0 = cheapest this route/cabin/month has been in 90 days
    history_days: int = 0
    return_option: dict | None = None  # a return award found in the same scan
    dropped: bool = False  # re-verification says it's gone, repriced, or sold out

    @property
    def age_days(self) -> float | None:
        try:
            seen = datetime.fromisoformat(self.c.updated_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        return (datetime.now(timezone.utc) - seen).total_seconds() / 86400

    @property
    def nonstop(self) -> bool | None:
        """True/False once flight details are known; None before that.

        seats.aero's `direct` flag only means "one flight number" -- EWR-JNB is
        flagged direct but stops twice -- so it is never used to pick a fare.
        """
        return None if self.trip is None else self.trip.nonstop

    @property
    def slow(self) -> bool:
        """Itinerary takes far longer than flying the distance nonstop would."""
        if not self.trip or not self.trip.duration_min or not self.c.distance:
            return False
        nonstop_min = self.c.distance / 500 * 60 + 45
        return self.trip.duration_min > max(1.8 * nonstop_min, nonstop_min + 300)

    @property
    def rank_notes(self) -> list[str]:
        """Why this deal was ranked below its dollar value, in plain words."""
        notes = []
        if self.trip and self.trip.mixed_cabin:
            notes.append("part of the trip is in a lower cabin")
        if self.trip and self.trip.airport_changes:
            notes.append("you'd change airports mid-trip")
        if self.slow:
            notes.append("much slower than flying direct")
        if (self.age_days or 0) > 5:
            notes.append(f"last confirmed {self.age_days:.0f} days ago")
        if self.rt_unavailable:
            notes.append("no round-trip fare could be priced, so this uses the one-way fare")
        if self.trip_unverified:
            notes.append("couldn't re-check it with seats.aero just now")
        return notes

    @property
    def rank_value(self) -> float:
        """Dollar surplus, discounted for things that make a deal worse than its CPP says."""
        v = self.surplus or 0.0
        if v <= 0:
            return v  # discounts on a negative surplus would rank a worse deal higher
        if self.trip and self.trip.mixed_cabin:
            v *= 0.4
        if self.trip and self.trip.airport_changes:
            v *= 0.5  # self-transfer between airports mid-trip
        if self.slow:
            v *= 0.75
        if (self.age_days or 0) > 5:
            v *= 0.85
        if self.history_pct is not None:
            # Nudge by how unusual this price is for this route, not just its size.
            v *= 1 + HISTORY_WEIGHT * (self.history_pct - 0.5) * 2
        if self.rt_unavailable:
            v *= NO_ROUND_TRIP_PENALTY
        return v

    @property
    def bookable_now(self) -> bool:
        return self.held_miles >= self.c.points

    @property
    def group_key(self) -> tuple:
        # One entry per destination+cabin: other dates, origins and programs for the
        # same trip become "other dates" / "alternatives" rather than separate rows.
        return (self.c.dest, self.c.cabin)

    @property
    def report_key(self) -> str:
        return "|".join([self.c.source, self.c.origin, self.c.dest, self.c.cabin,
                         str(self.c.points), self.c.date])

    @property
    def baseline(self) -> float:
        """What these points are normally worth, in cents."""
        return valuation.baseline_cpp(self.c.program, self.c.cabin)

    def surplus_vs(self, bar: float) -> float | None:
        if self.cash is None:
            return None
        return (self.cash - self.taxes_usd) - self.c.points * bar / 100

    def to_dict(self) -> dict:
        c = self.c
        return {
            "origin": c.origin, "dest": c.dest, "program": c.program, "source": c.source,
            "cabin": c.cabin, "date": c.date, "points": c.points,
            "taxes": round(c.taxes, 2), "currency": c.taxes_currency, "taxes_usd": round(self.taxes_usd, 2),
            "cash_price": self.cash, "cash_is_approx": self.cash_approx, "cash_cabin": price_cabin(c.cabin),
            "cash_basis": self.cash_basis, "same_carrier_cash": self.same_carrier_cash,
            "one_way_cash": self.one_way_cash, "round_trip_half": self.round_trip_half,
            "cpp": round(self.cpp, 3) if self.cpp is not None else None,
            "great_floor": self.floor, "baseline_cpp": self.baseline, "surplus_usd": round(self.surplus, 0) if self.surplus is not None else None,
            "est_cash": round(self.est.median_price), "est_cpp": round(self.est_cpp, 2),
            "p_great": round(self.p_great, 2), "seats": c.seats, "direct": c.direct, "airlines": c.airlines,
            "other_dates": self.other_dates, "alternatives": self.alternatives, "updated_at": c.updated_at,
            "id": c.id, "age_days": round(self.age_days, 1) if self.age_days is not None else None,
            "trip": self.trip.as_dict() if self.trip else None, "slow": self.slow,
            "rt_unavailable": self.rt_unavailable, "nonstop": self.nonstop,
            "trip_unverified": self.trip_unverified, "rank_notes": self.rank_notes,
            "history_pct": round(self.history_pct, 2) if self.history_pct is not None else None,
            "history_days": self.history_days, "return_option": self.return_option,
            "ranked_value_usd": round(self.rank_value) if self.surplus is not None else None,
            "held_miles": self.held_miles, "bookable_now": self.bookable_now,
            "top_up_needed": max(c.points - self.held_miles, 0) if self.held_miles else None,
        }


def score_candidates(cands: list[award_scanner.AwardCandidate], model: fare_model.FareModel,
                     watchlist: list[WatchEntry] | None = None,
                     program_balances: dict[str, int] | None = None) -> list[Scored]:
    est_cache: dict[tuple, fare_model.Estimate] = {}
    out = []
    # An award whose estimated value is far below its bar can never be priced or
    # reported; dropping it here keeps ~150k awards' worth of state manageable.
    keep_ratio = 0.5
    for c in cands:
        k = (c.origin, c.dest, price_cabin(c.cabin))
        if k not in est_cache:
            est_cache[k] = model.estimate(*k)
        est = est_cache[k]
        taxes_usd = c.taxes * valuation.fx_rate(c.taxes_currency)
        floor = valuation.great_floor(c.cabin, c.program)
        est_cpp = valuation.compute_cpp(est.median_price, taxes_usd, c.points) or 0.0
        # P(cash fare is high enough for this award to clear the bar)
        s = Scored(c, taxes_usd, est, floor, est_cpp, est.prob_at_least(floor * c.points / 100 + taxes_usd))
        s.held_miles = (program_balances or {}).get(c.program, 0)
        s.watch = [w for w in (watchlist or []) if w.matches(c)]
        if s.watch:
            bar = min(w.bar_for(c.cabin, c.program) for w in s.watch)
            s.p_watch = est.prob_at_least(bar * c.points / 100 + taxes_usd)
        if est_cpp < floor * keep_ratio and not s.watch and not s.bookable_now:
            continue
        out.append(s)
    return out


def _set_cash(s: Scored, cash: float) -> None:
    s.cash = cash
    s.cpp = valuation.compute_cpp(cash, s.taxes_usd, s.c.points)
    # Rank by dollars above what these points are normally worth, not above a flat
    # cabin bar: otherwise a program whose points are simply worth more wins by default.
    s.surplus = s.surplus_vs(s.baseline)


def _finalize_cash(s: Scored) -> None:
    """Value the award at the LOWER of the comparable one-way fare and half a round
    trip. Both inputs can arrive in either order (a round trip is priced first, then
    the fare is re-matched once the real stop count is known), so the choice is made
    here rather than at either call site.
    """
    if s.one_way_cash is None:
        return
    if s.round_trip_half is not None and s.round_trip_half < s.one_way_cash:
        s.cash_basis = (f"half of a {s.rt_stay_nights}-night round trip "
                        f"(the one-way fare is ${s.one_way_cash:,.0f})")
        _set_cash(s, s.round_trip_half)
    else:
        _set_cash(s, s.one_way_cash)


def _apply_quote(s: Scored, q: cash_quotes.Quote) -> None:
    """Value the award against a comparable one-way fare.

    Until flight details confirm a true nonstop, compare against the cheapest
    fare at any number of stops -- the conservative choice, since a connecting
    award shouldn't get credit for a pricier nonstop-grade fare.
    """
    comp = cash_quotes.comparable_fare(q, s.nonstop, s.c.airlines)
    if comp is None:
        return
    s.quote = q
    s.cash_approx, s.cash_basis, s.same_carrier_cash = q.approx, comp.basis, comp.same_carrier_price
    s.one_way_cash = comp.price
    _finalize_cash(s)


def _pricing_priority(s: Scored) -> float:
    upside = s.p_great * s.c.points * max(s.est_cpp - s.floor, 0.1)
    if s.watch:
        upside = max(upside, s.p_watch * s.c.points * 0.1) * WATCH_PRIORITY_BOOST
    return upside


def _warn_on_source_drop(scan: dict, previous: dict, log) -> None:
    """A program quietly disappearing from the scan is otherwise invisible."""
    before = (previous.get("scan") or {}).get("per_source") or {}
    now = scan.get("per_source") or {}
    for src, was in before.items():
        if was >= 100 and now.get(src, 0) < was * 0.5:
            log(f"::warning::{src} returned {now.get(src, 0):,} awards, down from {was:,} yesterday: "
                "the program may have dropped out of seats.aero or lost availability.")


def _warn_on_stale_training(rows: list[dict], log) -> None:
    """The fare model happily trains on months-old fares if quote collection stops."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = sum(1 for r in rows if (r.get("at") or "") >= cutoff)
    if recent < 20:
        log(f"::warning::Only {recent} of {len(rows)} fares used to train the estimates are from the "
            "last two weeks; cash-price collection may have stopped.")


def _warn_on_health(price: dict, scan: dict, budget: int, log) -> None:
    """Loud warnings for the ways a run can look fine and still be useless."""
    live = price.get("live", 0) + price.get("watch_live", 0) + price.get("explore_live", 0)
    if live and price.get("no_fare", 0) / live > 0.3:
        log(f"::warning::{price['no_fare']} of {live} cash lookups returned no fare: "
            "Google may be blocking or serving empty pages.")
    if budget and live < 0.5 * budget and price.get("skipped_low_p", 0) > 1000:
        log(f"::warning::Only {live} of a {budget} lookup budget was spent; this digest leans on "
            "reused fares.")
    if scan.get("rows") and scan.get("stale", 0) / scan["rows"] > 0.25:
        log(f"::warning::{scan['stale']:,} of {scan['rows']:,} seats.aero rows were past the "
            "freshness limit: its cache may be lagging.")


RETURN_MIN_NIGHTS = 3
RETURN_MAX_NIGHTS = 21


def attach_return_options(deals: list[Scored], scored: list[Scored]) -> int:
    """Find each reported one-way a matching return award from the same scan.

    Costs nothing (the awards are already scanned) and answers the obvious
    question a one-way deal raises: what would the whole trip cost?
    """
    index: dict[tuple, list[Scored]] = {}
    for s in scored:
        if s.c.seats >= 0:
            index.setdefault((s.c.source, s.c.origin, s.c.dest, s.c.cabin), []).append(s)
    found = 0
    for d in deals:
        out_date = datetime.strptime(d.c.date, "%Y-%m-%d").date()
        window = [(out_date + timedelta(days=RETURN_MIN_NIGHTS)).isoformat(),
                  (out_date + timedelta(days=RETURN_MAX_NIGHTS)).isoformat()]
        options = [r for r in index.get((d.c.source, d.c.dest, d.c.origin, d.c.cabin), [])
                   if window[0] <= r.c.date <= window[1]]
        if not options:
            continue
        best = min(options, key=lambda r: r.c.points)
        d.return_option = {"date": best.c.date, "points": best.c.points,
                           "taxes_usd": round(best.taxes_usd, 2), "program": best.c.program,
                           "round_trip_points": d.c.points + best.c.points}
        found += 1
    return found


def estimate_drift(errors: list[float]) -> dict:
    """Median and 90th-percentile error of the fare model against fresh real fares."""
    if not errors:
        return {}
    xs = sorted(errors)
    return {"n": len(xs), "median_pct": round(xs[len(xs) // 2] * 100, 1),
            "p90_pct": round(xs[min(int(len(xs) * 0.9), len(xs) - 1)] * 100, 1)}


def price_promising(scored: list[Scored], max_lookups: int, log=print, max_watch_lookups: int = 0,
                    watchlist: list[WatchEntry] | None = None) -> dict:
    """Attach real fares, spending live lookups on the highest expected value first.

    Watchlist matches get a first pass with their own budget, shared ROUND-ROBIN
    across entries (otherwise one entry with thousands of matches, like the
    Caribbean, takes it all). Then everything competes for max_lookups.
    """
    stats = {"cached": 0, "live": 0, "watch_live": 0, "explore_live": 0, "no_fare": 0, "failed": 0,
             "skipped_low_p": 0, "estimate_errors": []}
    state = {"quotes": cash_quotes.load(), "stop": False}
    if max_watch_lookups:
        groups = [[s for s in scored if any(x is w for x in s.watch)] for w in (watchlist or [])]
        groups.append([s for s in scored if s.bookable_now])  # awards you can book with miles already held
        queues = [sorted(g, key=lambda s: -_pricing_priority(s)) for g in groups if g]
        spent = 0
        while queues and spent < max_watch_lookups and not state["stop"]:
            for q in list(queues):
                while q:  # advance this entry until it spends one live lookup (cached/skipped are free)
                    if _price_one(q.pop(0), state, stats, log) == "live":
                        spent += 1
                        break
                if not q:
                    queues.remove(q)
                if spent >= max_watch_lookups or state["stop"]:
                    break
        stats["watch_live"], stats["live"] = stats["live"], 0
    explore_budget = int(max_lookups * EXPLORE_SHARE)
    if explore_budget:
        stats["explore_live"] = _explore_pass(scored, explore_budget, state, stats, log)
    live = 0
    for s in sorted((s for s in scored if s.cash is None), key=lambda s: -_pricing_priority(s)):
        if state["stop"]:
            break
        if live >= max_lookups:
            _price_one(s, state, stats, log, allow_live=False)
            continue
        if _price_one(s, state, stats, log) == "live":
            live += 1
    stats["live"] = live
    return stats


def reuse_holdout(scored: list[Scored], budget: int, log=print) -> dict:
    """Price a few dates next to an existing fresh quote, to measure what reuse costs.

    Reused quotes are never otherwise re-checked, so the error they introduce is
    invisible: saved quotes contain no same-route pairs within a week to compare.
    """
    quotes = cash_quotes.load()
    errors: list[float] = []
    seen_cells: set[tuple] = set()
    for s in scored:
        if len(errors) >= budget:
            break
        cell = (s.c.origin, s.c.dest, price_cabin(s.c.cabin))
        if cell in seen_cells:
            continue
        borrowed = cash_quotes.find_cached(quotes, s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
        if borrowed is None or not borrowed.approx or borrowed.price_usd is None:
            continue  # only interesting where a nearby-date quote would have been used
        try:
            fresh = cash_quotes.get_quote(s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin),
                                          allow_approx=False)
        except (cash_quotes.OutOfWindow, flight_search.NotConfigured, flight_search.SearchFailed):
            continue
        if fresh is None or fresh.price_usd is None:
            continue
        seen_cells.add(cell)
        errors.append(abs(fresh.price_usd - borrowed.price_usd) / fresh.price_usd)
    if not errors:
        return {}
    xs = sorted(errors)
    out = {"n": len(xs), "median_pct": round(xs[len(xs) // 2] * 100, 1),
           "max_pct": round(xs[-1] * 100, 1)}
    log(f"Reuse check: a borrowed fare was off by {out['median_pct']}% at the median, "
        f"{out['max_pct']}% at worst ({out['n']} samples)")
    return out


def _explore_pass(scored: list[Scored], budget: int, state: dict, stats: dict, log) -> int:
    """Spend part of the budget on route+cabin cells with no recent quote.

    The normal gate only prices awards the model already rates highly, so it never
    learns where it is wrong, and a fare spike on a route it rates cheap stays
    invisible. This samples the least-known cells: one award per cell, largest
    uncertainty x points first.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EXPLORE_STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = {(q["origin"], q["dest"], q["cabin"]) for q in state["quotes"]
              if q.get("fetched_at", "") >= cutoff}
    best_per_cell: dict[tuple, Scored] = {}
    for s in scored:
        if s.cash is not None:
            continue
        cell = (s.c.origin, s.c.dest, price_cabin(s.c.cabin))
        if cell in recent:
            continue
        cur = best_per_cell.get(cell)
        if cur is None or s.est.sigma_log * s.c.points > cur.est.sigma_log * cur.c.points:
            best_per_cell[cell] = s
    spent = 0
    for s in sorted(best_per_cell.values(), key=lambda s: -(s.est.sigma_log * s.c.points)):
        if spent >= budget or state["stop"]:
            break
        before = stats["live"]
        if _price_one(s, state, stats, log, force=True) == "live":
            spent += 1
        stats["live"] = before  # counted separately so the main pass keeps its budget
    if spent:
        log(f"Explored {spent} route/cabin(s) with no quote in the last {EXPLORE_STALE_DAYS} days")
    return spent


def _price_one(s: Scored, state: dict, stats: dict, log, allow_live: bool = True,
               force: bool = False) -> str:
    """Price one candidate from cache or live. Returns "cached", "live", or "skipped"."""
    if s.cash is not None:
        return "skipped"
    q = cash_quotes.find_cached(state["quotes"], s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
    if q is not None:
        if q.price_usd is not None:
            _apply_quote(s, q)
            stats["cached"] += 1
        return "cached"
    if not allow_live:
        return "skipped"
    if not force and (s.p_great < MIN_P_GREAT_TO_PRICE
                      and not (s.watch and s.p_watch >= MIN_P_WATCH_TO_PRICE)
                      and not (s.bookable_now and s.p_great >= MIN_P_WATCH_TO_PRICE)):
        stats["skipped_low_p"] += 1
        return "skipped"
    try:
        q = cash_quotes.get_quote(s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
        if q is not None:  # keep the in-memory cache current without re-reading the whole file
            state["quotes"].append({k: v for k, v in q.__dict__.items() if k != "approx"})
    except cash_quotes.OutOfWindow:
        return "skipped"
    except flight_search.QuotaExhausted as e:
        log(f"Stopping live lookups: {e}")
        state["stop"] = True
        return "skipped"
    except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
        stats["live"] += 1
        stats["failed"] += 1
        # Stop early only if EVERY attempt so far has failed (e.g. Google blocked the scraper).
        if stats["failed"] >= 5 and stats["failed"] == stats["live"] + stats["watch_live"]:
            log(f"Cash lookups failing ({e}); stopping.")
            state["stop"] = True
        return "live"
    stats["live"] += 1
    if q is None or q.price_usd is None:
        stats["no_fare"] += 1
    else:
        # Fresh fare for an award the model had estimated: track how far off it was,
        # so drift in the estimates (which decide what gets priced) shows up early.
        if s.est.median_price > 0:
            stats.setdefault("estimate_errors", []).append(
                abs(q.price_usd - s.est.median_price) / s.est.median_price)
        _apply_quote(s, q)
    return "live"


# ── round trip ───────────────────────────────────────────────────────────────
def apply_round_trip(s: Scored, rt_cache: dict, today: date_cls | None = None) -> bool:
    """Value the award against min(one-way fare, half a round trip).

    Tries a 7-night stay, then shorter ones so trips near the edge of the
    booking window still get checked; a one-way-only valuation is usually far
    too generous (one-way fares run 1.5-2x half a round trip), so when no round
    trip can be priced the deal is marked and ranked down instead.
    """
    if s.cash is None:
        return False
    today = today or datetime.now(timezone.utc).date()
    depart = datetime.strptime(s.c.date, "%Y-%m-%d").date()
    stays = [n for n in ROUND_TRIP_FALLBACK_STAYS
             if (depart + timedelta(days=n) - today).days <= cash_quotes.MAX_LOOKAHEAD_DAYS]
    if not stays:
        s.rt_unavailable = True
        return False
    stay = stays[0]
    ret = depart + timedelta(days=stay)
    key = (s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin), stay)
    if key not in rt_cache:
        try:
            rt_cache[key] = flight_search.search_round_trip_offers(
                s.c.origin, s.c.dest, s.c.date, ret.isoformat(), price_cabin(s.c.cabin))
        except flight_search.SearchFailed:
            rt_cache[key] = None
    offers = rt_cache[key]
    if not offers:
        s.rt_unavailable = True
        return False
    pool = ([o for o in offers if o.stops <= 1] if s.nonstop else offers) or offers
    s.round_trip_half = min(o.price_usd for o in pool) / 2
    s.rt_stay_nights = stay
    s.rt_unavailable = False
    _finalize_cash(s)
    return True


# ── trip details ─────────────────────────────────────────────────────────────
def attach_trips(scored: list[Scored], cache: dict) -> None:
    """Fetch flight-level detail (1 seats.aero call per award, cached per run)."""
    for s in scored:
        if s.trip is not None:
            continue
        k = (s.c.id, s.c.cabin, s.c.points)
        if k not in cache:
            try:
                cache[k] = award_trips.fetch(s.c.id, s.c.cabin, s.c.points)
            except award_trips.LookupFailed:
                cache[k] = "failed"  # don't retry within a run, don't call it gone either
        s.trip_unverified = cache[k] == "failed"
        s.trip = None if s.trip_unverified else cache[k]
        if s.trip is not None and s.quote is not None:
            _apply_quote(s, s.quote)  # redo the fare match now that the real stop count is known


def still_bookable(s: Scored, sources_reporting_seats: set[str]) -> bool:
    """Re-check an award against seats.aero right now: the scan's data can be days old.

    Drops awards that are gone, that have repriced above what the scan saw, or
    that show 0 seats left in a program that does report seat counts (American
    and some others always report 0, meaning "unknown").
    """
    if s.trip_unverified:
        return True  # couldn't check; keep it, flagged, rather than pretending it's gone
    if s.trip is None:
        return False
    if not s.trip.price_matches:
        return False
    return not (s.trip.seats == 0 and s.c.source in sources_reporting_seats)


# ── ranking ──────────────────────────────────────────────────────────────────
def group_leaders(scored: list[Scored], bar_fn=None) -> list[Scored]:
    """Best award per destination+cabin that clears its bar; the rest of the group
    becomes other_dates (same program+origin) or alternatives."""
    bar_fn = bar_fn or (lambda s: s.floor)
    # No seat-count filter: the scanner only keeps awards seats.aero marks available, and
    # some programs (e.g. American) always report 0 remaining seats, meaning "unknown".
    confirmed = [s for s in scored if s.cpp is not None and s.cpp >= bar_fn(s)]
    best: dict[tuple, Scored] = {}
    seen: dict[tuple, set] = {}
    for s in sorted(confirmed, key=lambda s: -s.surplus_vs(bar_fn(s))):
        g = best.get(s.group_key)
        combo = (s.c.source, s.c.origin)
        if g is None:
            best[s.group_key], seen[s.group_key] = s, {combo}
            s.other_dates, s.alternatives = [], []
        elif combo == (g.c.source, g.c.origin):
            if s.c.date != g.c.date and s.c.date not in g.other_dates:
                g.other_dates.append(s.c.date)
        elif combo not in seen[s.group_key]:
            seen[s.group_key].add(combo)
            g.alternatives.append(f"{s.c.origin} via {s.c.program} {s.c.points:,} pts ({s.cpp:.2f}¢ one-way)")
    leaders = sorted(best.values(), key=lambda s: -s.surplus_vs(bar_fn(s)))
    for s in leaders:
        s.other_dates.sort()
        s.alternatives = s.alternatives[:3]
    return leaders


def _cap_per_program(leaders: list[Scored], cap: int) -> list[Scored]:
    seen: dict[str, int] = {}
    out = []
    for s in leaders:
        n = seen.get(s.c.source, 0)
        if n < cap:
            seen[s.c.source] = n + 1
            out.append(s)
    return out


def pick_top(leaders: list[Scored], top: int, min_economy: int = 5) -> list[Scored]:
    # Dollar surplus always favours premium cabins; reserve slots for economy. The
    # per-program cap applies within each cabin bucket, or one program's business
    # deals would consume the cap before economy is considered at all.
    premium = _cap_per_program([s for s in leaders if s.c.cabin in ("BUSINESS", "FIRST")], MAX_PER_PROGRAM)
    economy = _cap_per_program([s for s in leaders if s.c.cabin not in ("BUSINESS", "FIRST")], MAX_PER_PROGRAM)
    n_econ = min(len(economy), min_economy)
    picked = premium[: max(top - n_econ, 0)] + economy[: top - min(len(premium), max(top - n_econ, 0))]
    return sorted(picked, key=lambda s: -s.rank_value)[:top]


def reprice_fresh(deals: list[Scored], log=print) -> dict:
    """Re-price each reported deal on its exact date, ignoring saved quotes.

    Reported deals are selected precisely because their fare came in high, and most
    are priced from a quote borrowed from a nearby date: re-checking 9 top deals by
    hand found all 9 lower, one by 68% (a 5.52c "deal" was really 1.36c). This is
    ~50 free lookups and removes the single largest error in the digest.
    """
    stats = {"repriced": 0, "changed": 0, "failed": 0}
    for s in deals:
        if s.cash is None:
            continue
        try:
            q = cash_quotes.get_quote(s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin),
                                      allow_approx=False)
        except (cash_quotes.OutOfWindow, flight_search.NotConfigured, flight_search.SearchFailed):
            stats["failed"] += 1
            continue
        if q is None or q.price_usd is None:
            stats["failed"] += 1
            continue
        before = s.cash
        stats["repriced"] += 1
        _apply_quote(s, q)  # keeps the round-trip floor via _finalize_cash
        if before and abs(s.cash - before) / before > 0.05:
            stats["changed"] += 1
    if stats["repriced"]:
        log(f"Fresh re-pricing of the shortlist: {stats['repriced']} re-checked, "
            f"{stats['changed']} moved more than 5%, {stats['failed']} unavailable")
    return stats


def verify_leaders(scored: list[Scored], candidates: list[Scored], rt_cache: dict | None,
                   trip_cache: dict | None, sources_reporting_seats: set[str],
                   round_trip: bool, bar_fn=None, max_passes: int = 3) -> list[Scored]:
    """Round-trip check + live re-verification, then regroup from the FULL scored
    list so a demoted leader doesn't take its whole destination down with it.

    Regrouping can promote a deal that was never checked, so this repeats until
    the leaders it returns have all been verified (or the pass limit is hit).
    """
    bar_fn = bar_fn or (lambda s: s.floor)
    limit = max(len(candidates), 1)
    pending = list(candidates)
    leaders: list[Scored] = []
    for _ in range(max_passes):
        if round_trip and rt_cache is not None:
            for s in pending:
                apply_round_trip(s, rt_cache)
        if trip_cache is not None:
            attach_trips(pending, trip_cache)
            for s in pending:
                if not still_bookable(s, sources_reporting_seats):
                    s.dropped = True
        kept = [s for s in scored if not s.dropped and s.cpp is not None and s.cpp >= bar_fn(s)]
        leaders = group_leaders(kept, bar_fn)
        pending = [s for s in leaders[:limit]
                   if (trip_cache is not None and s.trip is None)
                   or (round_trip and rt_cache is not None and s.round_trip_half is None
                       and not s.rt_unavailable)]
        if not pending:
            break
    return leaders


def shortlist(scored: list[Scored], top: int, min_economy: int = 5, rt_cache: dict | None = None,
              round_trip: bool = False, trip_cache: dict | None = None,
              sources_reporting_seats: set[str] | None = None) -> list[Scored]:
    leaders = group_leaders(scored)
    if round_trip or trip_cache is not None:
        premium = [s for s in leaders if s.c.cabin in ("BUSINESS", "FIRST")][: top + ROUND_TRIP_CHECK_EXTRA]
        economy = [s for s in leaders if s.c.cabin not in ("BUSINESS", "FIRST")][: max(min_economy + 5, top)]
        leaders = verify_leaders(scored, premium + economy, rt_cache, trip_cache,
                                 sources_reporting_seats or set(), round_trip)
    return pick_top(leaders, top, min_economy)


def held_miles_report(scored: list[Scored], rt_cache: dict, round_trip: bool = True,
                      trip_cache: dict | None = None,
                      sources_reporting_seats: set[str] | None = None) -> list[Scored]:
    """Best deals bookable outright with miles already sitting in a program."""
    mine = [s for s in scored if s.bookable_now]
    leaders = verify_leaders(mine, group_leaders(mine)[: HELD_MILES_DEALS + 4], rt_cache, trip_cache,
                             sources_reporting_seats or set(), round_trip)
    return sorted(leaders, key=lambda s: -s.rank_value)[:HELD_MILES_DEALS]


def watch_report(scored: list[Scored], watchlist: list[WatchEntry], rt_cache: dict,
                 round_trip: bool = True, trip_cache: dict | None = None,
                 sources_reporting_seats: set[str] | None = None) -> list[dict]:
    out = []
    for w in watchlist:
        mine = [s for s in scored if any(x is w for x in s.watch)]

        def bar(s, w=w):
            return w.bar_for(s.c.cabin, s.c.program)

        leaders = verify_leaders(mine, group_leaders(mine, bar)[: WATCH_DEALS_PER_ENTRY + 4], rt_cache,
                                 trip_cache, sources_reporting_seats or set(), round_trip, bar)
        leaders.sort(key=lambda s: -(s.surplus_vs(bar(s)) * (s.rank_value / s.surplus if s.surplus else 1)))
        deals = []
        for s in leaders[:WATCH_DEALS_PER_ENTRY]:
            d = s.to_dict()
            d["watch_bar"] = bar(s)
            d["watch_surplus_usd"] = round(s.surplus_vs(bar(s)))
            deals.append((s, d))
        out.append({"label": w.label, "matched_awards": len(mine), "bar_fn": bar,
                    "priced": sum(1 for s in mine if s.cpp is not None), "deals": deals})
    return out


# ── reporting ────────────────────────────────────────────────────────────────
def _write_json_atomic(path: str, data: dict) -> None:
    """A crash mid-write would leave unparseable JSON, losing the report history
    and re-emailing everything next run."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".digest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_digest() -> dict:
    try:
        with open(DIGEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def run(max_lookups: int, top: int, send_email: bool, include_planned: bool = False,
        round_trip: bool = True, log=print, max_watch_lookups: int = DEFAULT_MAX_WATCH_LOOKUPS,
        resend: bool = False, use_watchlist: bool = True) -> dict:
    flight_search.SERPAPI_MAX_CALLS = SERPAPI_CAP
    started = datetime.now(timezone.utc)
    config = award_scanner.load_config()
    watchlist = load_watchlist(config) if use_watchlist else []
    if watchlist:
        extra = sorted({d for w in watchlist for d in w.dests} - set(config["destinations"]))
        cabins = list(dict.fromkeys(config["cabins"] + [c for w in watchlist for c in (w.cabins or [])]))
        config = {**config, "destinations": config["destinations"] + extra, "cabins": cabins}
    digest_before = _load_digest()
    program_balances = ledger.load_program_balances()
    transferable = award_scanner.transferable_sources(include_planned)
    held_sources = award_scanner.sources_for_programs(program_balances)
    left = award_scanner.remaining_calls()
    if left is not None and left < 250:
        log(f"::warning::Only {left} seats.aero calls left today (a full scan needs ~210). "
            "The scan will cover what it can and stop.")
    cands, scan_stats = award_scanner.scan(config, include_planned=include_planned, extra_sources=held_sources)
    if not cands:
        log("::error::No awards scanned (seats.aero quota exhausted or the API is down); "
            "keeping the previous digest.")
        return _load_digest() or {"top": [], "held_miles": [], "watchlist": []}
    log(f"Scan: {scan_stats['candidates']:,} fresh awards from {scan_stats['calls']} seats.aero calls "
        f"({scan_stats['stale']:,} stale rows dropped; {scan_stats['rate_limit_remaining']} calls left today)")
    if scan_stats.get("quota_exhausted"):
        log("::warning::seats.aero's daily call quota ran out mid-scan; this digest covers only the "
            f"programs scanned so far ({', '.join(scan_stats['sources'][:6])}...). It resets 24h after "
            "the first call of the day.")
    if scan_stats.get("truncated"):
        log(f"WARNING: results cut off at the page limit for {', '.join(scan_stats['truncated'])}")

    history = award_history.record(cands)
    hist_series = award_history.series(history)
    log(f"Award history: {len(history)} day(s) kept, {len(history.get(max(history), {})):,} price "
        f"changes today, {sum(1 for v in hist_series.values() if len(v) >= award_history.MIN_DAYS_FOR_PERCENTILE):,} "
        "route/cabin/months with enough history to rank on")
    _warn_on_source_drop(scan_stats, digest_before, log)
    training_rows = fare_model.training_rows()
    _warn_on_stale_training(training_rows, log)
    model = fare_model.FareModel(training_rows)
    log(f"Fare model: {model.summary()}")
    if program_balances:
        unscannable = sorted(set(program_balances) - set(award_scanner._PARTNER_TO_SOURCE))
        log(f"Miles already held: {program_balances}"
            + (f" (not covered by seats.aero: {', '.join(unscannable)})" if unscannable else ""))
    # Programs that never report seat counts (American always says 0) must not be
    # treated as sold out; only trust a 0 from a program that reports seats elsewhere.
    sources_reporting_seats = {c.source for c in cands if c.seats > 0}
    scored = score_candidates(cands, model, watchlist, program_balances)
    # Programs scanned only because you hold miles there (e.g. American, Delta) can't be
    # topped up from your cards, so keep those awards only when your miles fully cover them.
    scored = [s for s in scored if s.c.source in transferable or s.bookable_now]
    promising = sum(1 for s in scored if s.p_great >= MIN_P_GREAT_TO_PRICE)
    log(f"Estimates: {len(scored):,} awards worth considering, {promising:,} with a "
        f">= {MIN_P_GREAT_TO_PRICE:.0%} chance of clearing their bar; "
        f"{sum(1 for s in scored if s.watch):,} match the watchlist")

    del cands  # the scan's raw rows aren't needed once scored
    price_stats = price_promising(scored, max_lookups, log=log, max_watch_lookups=max_watch_lookups,
                                  watchlist=watchlist)
    reuse = reuse_holdout(scored, HOLDOUT_LOOKUPS, log=log)
    drift = estimate_drift(price_stats.pop("estimate_errors", []))
    _warn_on_health(price_stats, scan_stats, max_lookups, log)
    log(f"Pricing: {price_stats}")
    if drift:
        log(f"Fare estimates vs fresh fares: {drift['median_pct']}% median error, "
            f"{drift['p90_pct']}% at the 90th percentile ({drift['n']} lookups)")
        if drift["median_pct"] > ESTIMATE_DRIFT_WARN_PCT:
            log(f"::warning::Fare estimates are drifting ({drift['median_pct']}% median error, "
                f"expected under {ESTIMATE_DRIFT_WARN_PCT}%): the deals picked for pricing may be "
                "poorly chosen. Check fare_model against recent cash_quotes.")

    digest = _load_digest()
    cutoff = (started - timedelta(days=REPORT_COOLDOWN_DAYS)).isoformat()
    reported = {k: v for k, v in digest.get("reported", {}).items() if v >= cutoff}
    # --resend emails everything currently listed, but must not erase the history:
    # a wiped history would re-email every deal again on the next normal run.
    already = {} if resend else reported

    rt_cache: dict = {}
    trip_cache: dict = {}
    ranked = shortlist(scored, top, rt_cache=rt_cache, round_trip=round_trip, trip_cache=trip_cache,
                       sources_reporting_seats=sources_reporting_seats)
    held = held_miles_report(scored, rt_cache, round_trip=round_trip, trip_cache=trip_cache,
                             sources_reporting_seats=sources_reporting_seats)
    watch = watch_report(scored, watchlist, rt_cache, round_trip=round_trip, trip_cache=trip_cache,
                         sources_reporting_seats=sources_reporting_seats)

    for s in scored:  # how unusual is this price for this route/cabin/month?
        s.history_pct, s.history_days = award_history.percentile(
            hist_series, award_history.key_for(s.c.source, s.c.origin, s.c.dest, s.c.cabin, s.c.date),
            s.c.points)

    if FRESH_PRICE_SHORTLIST:
        shortlisted = {id(s): s for s in ranked + held}
        shortlisted.update({id(s): s for g in watch for s, _ in g["deals"]})
        reprice_stats = reprice_fresh(list(shortlisted.values()), log=log)
        # Re-pricing can push a deal below its bar: rebuild the lists rather than
        # publishing a deal that no longer qualifies.
        ranked = pick_top([s for s in ranked if s.cpp is not None and s.cpp >= s.floor], top)
        held = sorted([s for s in held if s.cpp is not None and s.cpp >= s.floor],
                      key=lambda s: -s.rank_value)
        for g in watch:
            g["deals"] = [(s, s.to_dict()) for s, _ in g["deals"]
                          if s.cpp is not None and s.cpp >= g["bar_fn"](s)]
            for s, d in g["deals"]:
                d["watch_bar"] = g["bar_fn"](s)
                d["watch_surplus_usd"] = round(s.surplus_vs(g["bar_fn"](s)))
    else:
        reprice_stats = {}

    # NB: not `reported` -- that name holds the 14-day email history and is written
    # to the digest; clobbering it broke the digest write.
    reported_deals = ranked + held + [s for g in watch for s, _ in g["deals"]]
    with_returns = attach_return_options(reported_deals, scored)
    log(f"Return legs found for {with_returns} of {len(reported_deals)} reported deals")

    # Serialize now: watch_report regroups some of the same objects and rewrites
    # their other_dates/alternatives for its own subset.
    top_pairs = []
    for s in ranked:
        d = s.to_dict()
        d["new"] = s.report_key not in already
        top_pairs.append((s.report_key, d))
    held_pairs = []
    for s in held:
        d = s.to_dict()
        key = f"held:{s.report_key}"
        d["new"] = key not in already
        held_pairs.append((key, d))
    log(f"Round-trip checks: {len(rt_cache)} ({sum(1 for v in rt_cache.values() if v)} priced); "
        f"flight details: {len(trip_cache)} checked, "
        f"{sum(1 for v in trip_cache.values() if v is None)} gone/repriced, "
        f"{sum(1 for v in trip_cache.values() if v and v.mixed_cabin)} mixed cabin")
    watch_out, watch_pairs = [], []
    for g in watch:
        for s, d in g["deals"]:
            key = f"watch:{g['label']}|{s.report_key}"
            d["watch_label"], d["new"] = g["label"], key not in already
            watch_pairs.append((key, d))
        watch_out.append({"label": g["label"], "matched_awards": g["matched_awards"],
                          "priced": g["priced"], "deals": [d for _, d in g["deals"]]})

    import funding
    balances = ledger.load_balances()
    for _, d in top_pairs + held_pairs + watch_pairs:
        d["pay_summary"] = funding.plan(d["program"], d["points"], balances, program_balances).summary
    fresh_watch = sorted([(k, d) for k, d in watch_pairs if d["new"]], key=lambda kd: -kd[1]["watch_surplus_usd"])
    fresh = ([(k, d) for k, d in held_pairs if d["new"]] + fresh_watch[:WATCH_EMAIL_MAX]
             + [(k, d) for k, d in top_pairs if d["new"]])
    emailed = 0
    out_email_failed: list[str] = []
    if send_email and fresh and not deal_email.is_configured():
        log("::warning::Gmail isn't configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD): no digest email sent.")
    if send_email and fresh and deal_email.is_configured():
        held_new = [d for k, d in fresh if k.startswith("held:")]
        watch_new = [d for k, d in fresh if k.startswith("watch:")]
        top_new = [d for k, d in fresh if not k.startswith(("held:", "watch:"))]
        n_unique = len({k.split(":", 1)[-1] for k, _ in fresh})  # same award can appear in 2 sections
        lead = max((d for _, d in fresh), key=lambda d: d.get("surplus_usd") or 0)  # biggest, not first
        import places
        try:
            deal_email.send_digest_email(
                [("✅ Book now with miles you already have",
                  "Covered by miles already in your airline accounts: no transfer needed.", held_new),
                 ("⭐ Your watchlist", "Destinations you asked to watch, in their best seasons.", watch_new),
                 ("🏆 Top deals", "The best value across all your routes, ranked by dollars saved.", top_new)],
                subject=(f"✈️ {n_unique} new award deal{'s' if n_unique != 1 else ''}: "
                         f"{places.city(lead['dest'])} {lead['cpp']:.1f}¢/pt"
                         + (f", {len(held_new)} bookable with miles you have" if held_new else "")),
                intro=(f"Picked from {scan_stats['candidates']:,} award seats on seats.aero. Every deal below "
                       "was re-priced today on its own date against Google Flights, and valued at the "
                       "lower of the one-way fare and half a round trip. Awards the estimate rated "
                       "unpromising were never priced, so a bargain can still be missed."),
            )
            emailed = n_unique
            for k, _ in fresh:
                reported.setdefault(k, started.isoformat())
        except Exception as e:
            log(f"::error::Deal Finder could not send the digest email: {type(e).__name__}: {e}")
            out_email_failed.append(str(e))

    public = {"pay_summary", "held_miles", "top_up_needed", "bookable_now"}  # keep balances private
    out = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan": scan_stats,
        "model": model.summary(),
        "pricing": price_stats,
        "repricing": reprice_stats,
        "estimate_drift": drift,
        "reuse_error": reuse,
        "round_trip_checks": len(rt_cache),
        "held_miles": [{k: v for k, v in d.items() if k not in public} for _, d in held_pairs],
        "watchlist": [{**g, "deals": [{k: v for k, v in d.items() if k not in public} for d in g["deals"]]}
                      for g in watch_out],
        "top": [{k: v for k, v in d.items() if k not in public} for _, d in top_pairs],
        "emailed_new": emailed, "email_failed": out_email_failed,
        "reported": reported,
    }
    # The site recomputes "how to pay" locally from your balances, so the public
    # digest never carries them.
    _write_json_atomic(DIGEST_PATH, out)
    return out


def preview_sources(sources: list[str], max_lookups: int = 60, top: int = 10, log=print) -> dict:
    """Best deals in specific programs you can't reach today (e.g. what a new card would
    unlock), on your usual routes. No watchlist, no digest, no email."""
    flight_search.SERPAPI_MAX_CALLS = 0
    config = {**award_scanner.load_config(), "sources": sources}
    if not sources:
        return {"deals": [], "candidates": 0, "calls": 0}
    cands, stats = award_scanner.scan(config, extra_sources=sources)
    log(f"Found {stats['candidates']:,} awards in {', '.join(sources)} ({stats['calls']} seats.aero calls). Pricing…")
    scored = [s for s in score_candidates(cands, fare_model.FareModel()) if s.c.source in sources]
    price_promising(scored, max_lookups, log=log)
    ranked = shortlist(scored, top, rt_cache={}, round_trip=True, trip_cache={})
    return {"deals": [s.to_dict() for s in ranked], "candidates": stats["candidates"], "calls": stats["calls"]}


def _line(i: int, d: dict) -> str:
    more = f" +{len(d['other_dates'])}d" if d["other_dates"] else ""
    rt = "RT" if d.get("round_trip_half") is not None and d["cash_price"] == d["round_trip_half"] else "  "
    return (f"{i:>2}. {d['origin']}-{d['dest']} {d['cabin'][:4]} {d['program'][:24]:24} {d['date']}{more:6} "
            f"{d['points']:>7,} + ${d['taxes_usd']:.0f} vs ${d['cash_price']:,.0f} {rt} {d['cpp']:.2f}¢ "
            f"+${d.get('watch_surplus_usd', d['surplus_usd']):,.0f}{'  NEW' if d['new'] else ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-lookups", type=int, default=DEFAULT_MAX_LOOKUPS)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--max-watch-lookups", type=int, default=DEFAULT_MAX_WATCH_LOOKUPS)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--resend", action="store_true",
                    help="email every current deal, ignoring the 14-day already-reported cooldown (for testing)")
    ap.add_argument("--no-round-trip", action="store_true", help="skip the round-trip fare check")
    ap.add_argument("--include-planned", action="store_true",
                    help="also scan programs reachable only from cards you plan to get")
    args = ap.parse_args(argv)
    try:
        out = run(args.max_lookups, args.top, not args.no_email, args.include_planned, not args.no_round_trip,
                  max_watch_lookups=args.max_watch_lookups, resend=args.resend)
    except (seats_aero.SearchFailed, seats_aero.NotConfigured) as e:
        print(f"::error::seats.aero is unavailable: {e}")
        return 1
    if out.get("email_failed"):
        return 1  # fail the scheduled run so GitHub tells you the email didn't go out
    if out["held_miles"]:
        print("\n✅ Book now with miles you already hold:")
        for i, d in enumerate(out["held_miles"], 1):
            print(_line(i, d))
    for g in out["watchlist"]:
        print(f"\n⭐ {g['label']}: {len(g['deals'])} deal(s) from {g['matched_awards']:,} matching awards "
              f"({g['priced']} priced)")
        for i, d in enumerate(g["deals"], 1):
            print(_line(i, d))
    print(f"\nTop {len(out['top'])} (RT = valued at half a round trip):")
    for i, d in enumerate(out["top"], 1):
        print(_line(i, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
