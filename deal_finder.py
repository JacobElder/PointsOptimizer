"""
Deal finder: separates the wheat from the chaff in seats.aero award space.

Pipeline (one run, ~3-5 minutes, ~20 seats.aero calls, 0 SerpApi calls normally):
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

import award_scanner
import cash_quotes
import check_alerts
import deal_email
import deal_log
import fare_model
import flight_search
import ledger

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
ROUND_TRIP_CHECK_EXTRA = 15  # also round-trip-check this many runners-up, since leaders can drop out
WATCH_DEALS_PER_ENTRY = 2
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
    bar: float | None = None  # CPP bar; None = the cabin's usual great-deal bar

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

    def bar_for(self, cabin: str) -> float:
        return self.bar if self.bar is not None else deal_log.great_floor(cabin)


def load_watchlist(config: dict) -> list[WatchEntry]:
    return [WatchEntry.from_config(w) for w in config.get("watchlist", []) if w.get("dests")]


# ── scoring ──────────────────────────────────────────────────────────────────
@dataclass(eq=False)
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
        return "|".join([self.c.source, self.c.origin, self.c.dest, self.c.cabin, str(self.c.points)])

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
            "great_floor": self.floor, "surplus_usd": round(self.surplus, 0) if self.surplus is not None else None,
            "est_cash": round(self.est.median_price), "est_cpp": round(self.est_cpp, 2),
            "p_great": round(self.p_great, 2), "seats": c.seats, "direct": c.direct, "airlines": c.airlines,
            "other_dates": self.other_dates, "alternatives": self.alternatives, "updated_at": c.updated_at,
            "held_miles": self.held_miles, "bookable_now": self.bookable_now,
            "top_up_needed": max(c.points - self.held_miles, 0) if self.held_miles else None,
        }


def score_candidates(cands: list[award_scanner.AwardCandidate], model: fare_model.FareModel,
                     watchlist: list[WatchEntry] | None = None,
                     program_balances: dict[str, int] | None = None) -> list[Scored]:
    est_cache: dict[tuple, fare_model.Estimate] = {}
    out = []
    for c in cands:
        k = (c.origin, c.dest, price_cabin(c.cabin))
        if k not in est_cache:
            est_cache[k] = model.estimate(*k)
        est = est_cache[k]
        taxes_usd = c.taxes * check_alerts._fx_rate(c.taxes_currency)
        floor = deal_log.great_floor(c.cabin)
        est_cpp = check_alerts.compute_cpp(est.median_price, taxes_usd, c.points) or 0.0
        # P(cash fare is high enough for this award to clear the bar)
        s = Scored(c, taxes_usd, est, floor, est_cpp, est.prob_at_least(floor * c.points / 100 + taxes_usd))
        s.held_miles = (program_balances or {}).get(c.program, 0)
        s.watch = [w for w in (watchlist or []) if w.matches(c)]
        if s.watch:
            bar = min(w.bar_for(c.cabin) for w in s.watch)
            s.p_watch = est.prob_at_least(bar * c.points / 100 + taxes_usd)
        out.append(s)
    return out


def _set_cash(s: Scored, cash: float) -> None:
    s.cash = cash
    s.cpp = check_alerts.compute_cpp(cash, s.taxes_usd, s.c.points)
    s.surplus = s.surplus_vs(s.floor)


def _apply_quote(s: Scored, q: cash_quotes.Quote) -> None:
    comp = cash_quotes.comparable_fare(q, s.c.direct, s.c.airlines)
    if comp is None:
        return
    s.cash_approx, s.cash_basis, s.same_carrier_cash = q.approx, comp.basis, comp.same_carrier_price
    s.one_way_cash = comp.price
    _set_cash(s, comp.price)


def _pricing_priority(s: Scored) -> float:
    upside = s.p_great * s.c.points * max(s.est_cpp - s.floor, 0.1)
    if s.watch:
        upside = max(upside, s.p_watch * s.c.points * 0.1) * WATCH_PRIORITY_BOOST
    return upside


def price_promising(scored: list[Scored], max_lookups: int, log=print, max_watch_lookups: int = 0,
                    watchlist: list[WatchEntry] | None = None) -> dict:
    """Attach real fares, spending live lookups on the highest expected value first.

    Watchlist matches get a first pass with their own budget, shared ROUND-ROBIN
    across entries (otherwise one entry with thousands of matches, like the
    Caribbean, takes it all). Then everything competes for max_lookups.
    """
    stats = {"cached": 0, "live": 0, "watch_live": 0, "no_fare": 0, "failed": 0, "skipped_low_p": 0}
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


def _price_one(s: Scored, state: dict, stats: dict, log, allow_live: bool = True) -> str:
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
    if (s.p_great < MIN_P_GREAT_TO_PRICE and not (s.watch and s.p_watch >= MIN_P_WATCH_TO_PRICE)
            and not (s.bookable_now and s.p_great >= MIN_P_WATCH_TO_PRICE)):
        stats["skipped_low_p"] += 1
        return "skipped"
    try:
        q = cash_quotes.get_quote(s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
        state["quotes"] = cash_quotes.load()
    except cash_quotes.OutOfWindow:
        return "skipped"
    except flight_search.QuotaExhausted as e:
        log(f"Stopping live lookups: {e}")
        state["stop"] = True
        return "skipped"
    except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
        stats["live"] += 1
        stats["failed"] += 1
        if stats["failed"] >= 5 and stats["live"] == 0 and stats["watch_live"] == 0:
            log(f"Cash lookups failing ({e}); stopping.")
            state["stop"] = True
        return "live"
    stats["live"] += 1
    if q is None or q.price_usd is None:
        stats["no_fare"] += 1
    else:
        _apply_quote(s, q)
    return "live"


# ── round trip ───────────────────────────────────────────────────────────────
def apply_round_trip(s: Scored, rt_cache: dict, today: date_cls | None = None) -> bool:
    """Value the award against min(one-way fare, half a 7-night round trip).
    Returns True if a round-trip price was obtained. Never spends SerpApi quota."""
    if s.cash is None:
        return False
    today = today or datetime.now(timezone.utc).date()
    ret = datetime.strptime(s.c.date, "%Y-%m-%d").date() + timedelta(days=ROUND_TRIP_STAY_DAYS)
    if (ret - today).days > cash_quotes.MAX_LOOKAHEAD_DAYS:
        return False
    key = (s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
    if key not in rt_cache:
        try:
            rt_cache[key] = flight_search.search_round_trip_offers(
                s.c.origin, s.c.dest, s.c.date, ret.isoformat(), price_cabin(s.c.cabin))
        except flight_search.SearchFailed:
            rt_cache[key] = None
    offers = rt_cache[key]
    if not offers:
        return False
    pool = ([o for o in offers if o.stops <= 1] if s.c.direct else offers) or offers
    half = min(o.price_usd for o in pool) / 2
    s.round_trip_half = half
    if s.one_way_cash is not None and half < s.one_way_cash:
        s.cash_basis = f"half of a {ROUND_TRIP_STAY_DAYS}-night round trip (lower than the one-way fare)"
        _set_cash(s, half)
    return True


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


def pick_top(leaders: list[Scored], top: int, min_economy: int = 5) -> list[Scored]:
    # Dollar surplus always favours premium cabins; reserve slots for economy.
    premium = [s for s in leaders if s.c.cabin in ("BUSINESS", "FIRST")]
    economy = [s for s in leaders if s.c.cabin not in ("BUSINESS", "FIRST")]
    n_econ = min(len(economy), min_economy)
    picked = premium[: top - n_econ] + economy[: top - min(len(premium), top - n_econ)]
    return sorted(picked, key=lambda s: -s.surplus)[:top]


def shortlist(scored: list[Scored], top: int, min_economy: int = 5, rt_cache: dict | None = None,
              round_trip: bool = False) -> list[Scored]:
    leaders = group_leaders(scored)
    if round_trip:
        cache = rt_cache if rt_cache is not None else {}
        premium = [s for s in leaders if s.c.cabin in ("BUSINESS", "FIRST")][: top + ROUND_TRIP_CHECK_EXTRA]
        economy = [s for s in leaders if s.c.cabin not in ("BUSINESS", "FIRST")][: min_economy + 5]
        for s in premium + economy:
            apply_round_trip(s, cache)
        leaders = sorted([s for s in premium + economy if s.cpp is not None and s.cpp >= s.floor],
                         key=lambda s: -s.surplus)
    return pick_top(leaders, top, min_economy)


def held_miles_report(scored: list[Scored], rt_cache: dict, round_trip: bool = True) -> list[Scored]:
    """Best deals bookable outright with miles already sitting in a program."""
    leaders = group_leaders([s for s in scored if s.bookable_now])[: HELD_MILES_DEALS + 3]
    if round_trip:
        for s in leaders:
            apply_round_trip(s, rt_cache)
    leaders = [s for s in leaders if s.cpp is not None and s.cpp >= s.floor]
    return sorted(leaders, key=lambda s: -s.surplus)[:HELD_MILES_DEALS]


def watch_report(scored: list[Scored], watchlist: list[WatchEntry], rt_cache: dict,
                 round_trip: bool = True) -> list[dict]:
    out = []
    for w in watchlist:
        mine = [s for s in scored if any(x is w for x in s.watch)]

        def bar(s, w=w):
            return w.bar_for(s.c.cabin)

        leaders = group_leaders(mine, bar)[: WATCH_DEALS_PER_ENTRY + 3]
        if round_trip:
            for s in leaders:
                apply_round_trip(s, rt_cache)
        leaders = [s for s in leaders if s.cpp is not None and s.cpp >= bar(s)]
        leaders.sort(key=lambda s: -s.surplus_vs(bar(s)))
        deals = []
        for s in leaders[:WATCH_DEALS_PER_ENTRY]:
            d = s.to_dict()
            d["watch_bar"] = bar(s)
            d["watch_surplus_usd"] = round(s.surplus_vs(bar(s)))
            deals.append((s, d))
        out.append({"label": w.label, "matched_awards": len(mine),
                    "priced": sum(1 for s in mine if s.cpp is not None), "deals": deals})
    return out


# ── reporting ────────────────────────────────────────────────────────────────
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
    program_balances = ledger.load_program_balances()
    transferable = award_scanner.transferable_sources(include_planned)
    held_sources = award_scanner.sources_for_programs(program_balances)
    cands, scan_stats = award_scanner.scan(config, include_planned=include_planned, extra_sources=held_sources)
    log(f"Scan: {scan_stats['candidates']:,} fresh awards from {scan_stats['calls']} seats.aero calls "
        f"({scan_stats['stale']:,} stale rows dropped; {scan_stats['rate_limit_remaining']} calls left today)")
    if scan_stats.get("truncated"):
        log(f"WARNING: results cut off at the page limit for {', '.join(scan_stats['truncated'])}")

    model = fare_model.FareModel()
    log(f"Fare model: {model.summary()}")
    if program_balances:
        unscannable = sorted(set(program_balances) - set(award_scanner._PARTNER_TO_SOURCE))
        log(f"Miles already held: {program_balances}"
            + (f" (not covered by seats.aero: {', '.join(unscannable)})" if unscannable else ""))
    scored = score_candidates(cands, model, watchlist, program_balances)
    # Programs scanned only because you hold miles there (e.g. American, Delta) can't be
    # topped up from your cards, so keep those awards only when your miles fully cover them.
    scored = [s for s in scored if s.c.source in transferable or s.bookable_now]
    promising = sum(1 for s in scored if s.p_great >= MIN_P_GREAT_TO_PRICE)
    log(f"Estimates: {promising:,} candidates >= {MIN_P_GREAT_TO_PRICE:.0%} chance of clearing the bar; "
        f"{sum(1 for s in scored if s.watch):,} match the watchlist")

    price_stats = price_promising(scored, max_lookups, log=log, max_watch_lookups=max_watch_lookups,
                                  watchlist=watchlist)
    log(f"Pricing: {price_stats}")

    digest = _load_digest()
    cutoff = (started - timedelta(days=REPORT_COOLDOWN_DAYS)).isoformat()
    reported = {} if resend else {k: v for k, v in digest.get("reported", {}).items() if v >= cutoff}

    rt_cache: dict = {}
    ranked = shortlist(scored, top, rt_cache=rt_cache, round_trip=round_trip)
    # Serialize now: watch_report regroups some of the same objects and rewrites
    # their other_dates/alternatives for its own subset.
    top_pairs = []
    for s in ranked:
        d = s.to_dict()
        d["new"] = s.report_key not in reported
        top_pairs.append((s.report_key, d))
    held_pairs = []
    for s in held_miles_report(scored, rt_cache, round_trip=round_trip):
        d = s.to_dict()
        key = f"held:{s.report_key}"
        d["new"] = key not in reported
        held_pairs.append((key, d))
    watch = watch_report(scored, watchlist, rt_cache, round_trip=round_trip)
    log(f"Round-trip checks: {len(rt_cache)} ({sum(1 for v in rt_cache.values() if v)} priced)")
    watch_out, watch_pairs = [], []
    for g in watch:
        for s, d in g["deals"]:
            key = f"watch:{g['label']}|{s.report_key}"
            d["watch_label"], d["new"] = g["label"], key not in reported
            watch_pairs.append((key, d))
        watch_out.append({"label": g["label"], "matched_awards": g["matched_awards"],
                          "priced": g["priced"], "deals": [d for _, d in g["deals"]]})

    fresh_watch = sorted([(k, d) for k, d in watch_pairs if d["new"]], key=lambda kd: -kd[1]["watch_surplus_usd"])
    fresh = ([(k, d) for k, d in held_pairs if d["new"]] + fresh_watch[:WATCH_EMAIL_MAX]
             + [(k, d) for k, d in top_pairs if d["new"]])
    emailed = 0
    if send_email and fresh and deal_email.is_configured():
        held_new = [d for k, d in fresh if k.startswith("held:")]
        watch_new = [d for k, d in fresh if k.startswith("watch:")]
        top_new = [d for k, d in fresh if not k.startswith(("held:", "watch:"))]
        lead = (held_new + watch_new + top_new)[0]
        import places
        try:
            deal_email.send_digest_email(
                [("✅ Book now with miles you already have",
                  "Covered by miles already in your airline accounts: no transfer needed.", held_new),
                 ("⭐ Your watchlist", "Destinations you asked to watch, in their best seasons.", watch_new),
                 ("🏆 Top deals", "The best value across all your routes, ranked by dollars saved.", top_new)],
                subject=(f"✈️ {len(fresh)} new award deal{'s' if len(fresh) != 1 else ''}: "
                         f"{places.city(lead['dest'])} {lead['cpp']:.1f}¢/pt"
                         + (f", {len(held_new)} bookable with miles you have" if held_new else "")),
                intro=(f"Found in {scan_stats['candidates']:,} award seats on seats.aero, each valued against a live "
                       "Google Flights fare (the lower of the one-way fare and half a round trip)."),
            )
            emailed = len(fresh)
            for k, _ in fresh:
                reported.setdefault(k, started.isoformat())
        except Exception as e:
            log(f"Email failed: {e}")

    out = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan": scan_stats,
        "model": model.summary(),
        "pricing": price_stats,
        "round_trip_checks": len(rt_cache),
        "held_miles": [d for _, d in held_pairs],
        "watchlist": watch_out,
        "top": [d for _, d in top_pairs],
        "emailed_new": emailed,
        "reported": reported,
    }
    with open(DIGEST_PATH, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    return out


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
    out = run(args.max_lookups, args.top, not args.no_email, args.include_planned, not args.no_round_trip,
              max_watch_lookups=args.max_watch_lookups, resend=args.resend)
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
