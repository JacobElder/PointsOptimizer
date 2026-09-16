"""
Deal finder: separates the wheat from the chaff in seats.aero award space.

Pipeline (one run, ~2-4 minutes, ~20 seats.aero calls, 0 SerpApi calls normally):
  1. SCAN      award_scanner: every fresh award on scan_config.json's routes, in
               programs your active pools can transfer to (typically 10k-20k rows).
  2. ESTIMATE  fare_model: estimated cash fare + uncertainty for every candidate,
               giving est. CPP and P(clears the cabin's "great" bar).
  3. PRICE     cash_quotes (free Google Flights via fast-flights): real fares for
               the most promising candidates, most valuable first. One quote also
               covers dates within +/-7 days on the same route+cabin.
  4. RANK      confirmed deals by dollars saved ABOVE the great bar:
                   surplus = (cash - taxes) - points * great_floor / 100
               then collapse repeats (same program/route/cabin) to the best date.
  5. REPORT    deal_digest.json (shown on Deal Radar) + an email of deals not
               reported in the last REPORT_COOLDOWN_DAYS.

Usage:
    python deal_finder.py                 # scan, price, save digest, email new deals
    python deal_finder.py --no-email      # everything except the email
    python deal_finder.py --max-lookups 40 --top 15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import award_scanner
import cash_quotes
import check_alerts
import deal_email
import deal_log
import fare_model
import flight_search

_BASE = os.path.dirname(os.path.abspath(__file__))
DIGEST_PATH = os.path.join(_BASE, "deal_digest.json")

MIN_P_GREAT_TO_PRICE = 0.2
DEFAULT_MAX_LOOKUPS = 120
DEFAULT_TOP = 20
REPORT_COOLDOWN_DAYS = 14
SERPAPI_CAP = 5


def price_cabin(cabin: str) -> str:
    """Cabin to compare cash fares against. Google's "first" search returns
    business or mixed-cabin itineraries on the many routes with no true first
    cabin (e.g. EWR-DEL "first" = a United business fare), which would inflate
    CPP. First-class awards are valued against the business fare instead:
    conservative, since a real first fare is always higher."""
    return "BUSINESS" if cabin == "FIRST" else cabin


@dataclass
class Scored:
    c: award_scanner.AwardCandidate
    taxes_usd: float
    est: fare_model.Estimate
    floor: float
    est_cpp: float
    p_great: float
    cash: float | None = None
    cash_approx: bool = False
    cash_basis: str = ""
    same_carrier_cash: float | None = None
    cpp: float | None = None
    surplus: float | None = None
    other_dates: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)

    @property
    def group_key(self) -> tuple:
        # One entry per destination+cabin: other dates, origins and programs for the
        # same trip become "other dates" / "alternatives" rather than separate rows.
        return (self.c.dest, self.c.cabin)

    @property
    def report_key(self) -> str:
        # Same program/route/cabin at the same points level = same deal for reporting.
        return "|".join([self.c.source, self.c.origin, self.c.dest, self.c.cabin, str(self.c.points)])

    def to_dict(self) -> dict:
        c = self.c
        return {
            "origin": c.origin, "dest": c.dest, "program": c.program, "source": c.source,
            "cabin": c.cabin, "date": c.date, "points": c.points,
            "taxes": round(c.taxes, 2), "currency": c.taxes_currency, "taxes_usd": round(self.taxes_usd, 2),
            "cash_price": self.cash, "cash_is_approx": self.cash_approx, "cash_cabin": price_cabin(c.cabin),
            "cash_basis": self.cash_basis, "same_carrier_cash": self.same_carrier_cash,
            "cpp": round(self.cpp, 3) if self.cpp is not None else None,
            "great_floor": self.floor, "surplus_usd": round(self.surplus, 0) if self.surplus is not None else None,
            "est_cash": round(self.est.median_price), "est_cpp": round(self.est_cpp, 2),
            "p_great": round(self.p_great, 2), "seats": c.seats, "direct": c.direct, "airlines": c.airlines,
            "other_dates": self.other_dates, "alternatives": self.alternatives, "updated_at": c.updated_at,
        }


def score_candidates(cands: list[award_scanner.AwardCandidate], model: fare_model.FareModel) -> list[Scored]:
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
        # Cash fare needed for this award to clear the great bar.
        needed = floor * c.points / 100 + taxes_usd
        out.append(Scored(c, taxes_usd, est, floor, est_cpp, est.prob_at_least(needed)))
    return out


def _apply_quote(s: Scored, q: cash_quotes.Quote) -> None:
    comp = cash_quotes.comparable_fare(q, s.c.direct, s.c.airlines)
    if comp is None:
        return
    s.cash, s.cash_approx, s.cash_basis, s.same_carrier_cash = comp.price, q.approx, comp.basis, comp.same_carrier_price
    s.cpp = check_alerts.compute_cpp(comp.price, s.taxes_usd, s.c.points)
    s.surplus = (comp.price - s.taxes_usd) - s.c.points * s.floor / 100


def price_promising(scored: list[Scored], max_lookups: int, log=print) -> dict:
    """Attach real fares, spending live lookups on the highest expected value first."""
    stats = {"cached": 0, "live": 0, "no_fare": 0, "failed": 0, "skipped_low_p": 0}
    quotes = cash_quotes.load()
    # Expected dollars above the bar if we price it: P(great) x typical upside.
    order = sorted(scored, key=lambda s: -(s.p_great * s.c.points * max(s.est_cpp - s.floor, 0.1)))
    for s in order:
        q = cash_quotes.find_cached(quotes, s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
        if q is not None:
            if q.price_usd is not None:
                _apply_quote(s, q)
                stats["cached"] += 1
            continue
        if s.p_great < MIN_P_GREAT_TO_PRICE:
            stats["skipped_low_p"] += 1
            continue
        if stats["live"] >= max_lookups:
            continue
        try:
            q = cash_quotes.get_quote(s.c.origin, s.c.dest, s.c.date, price_cabin(s.c.cabin))
            stats["live"] += 1
            quotes = cash_quotes.load()
        except cash_quotes.OutOfWindow:
            continue
        except flight_search.QuotaExhausted as e:
            log(f"Stopping live lookups: {e}")
            max_lookups = stats["live"]
            continue
        except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
            stats["failed"] += 1
            if stats["failed"] >= 5 and stats["live"] == 0:
                log(f"Cash lookups failing ({e}); stopping.")
                break
            continue
        if q is None or q.price_usd is None:
            stats["no_fare"] += 1
            continue
        _apply_quote(s, q)
    return stats


def shortlist(scored: list[Scored], top: int, min_economy: int = 5) -> list[Scored]:
    confirmed = [s for s in scored if s.cpp is not None and s.cpp >= s.floor and s.c.seats > 0]
    best: dict[tuple, Scored] = {}
    alt_seen: dict[tuple, set] = {}
    for s in sorted(confirmed, key=lambda s: -s.surplus):
        g = best.get(s.group_key)
        if g is None:
            best[s.group_key] = s
            alt_seen[s.group_key] = {(s.c.source, s.c.origin)}
            continue
        combo = (s.c.source, s.c.origin)
        if combo == (g.c.source, g.c.origin):
            g.other_dates.append(s.c.date)
        elif combo not in alt_seen[s.group_key]:
            alt_seen[s.group_key].add(combo)
            g.alternatives.append(f"{s.c.origin} via {s.c.program} {s.c.points:,} pts ({s.cpp:.2f}¢)")
    ranked = sorted(best.values(), key=lambda s: -s.surplus)
    for s in ranked:
        s.other_dates = sorted(set(s.other_dates) - {s.c.date})
        s.alternatives = s.alternatives[:3]
    # Dollar surplus always favours premium cabins; reserve slots so standout
    # economy redemptions still surface.
    premium = [s for s in ranked if s.c.cabin in ("BUSINESS", "FIRST")]
    economy = [s for s in ranked if s.c.cabin not in ("BUSINESS", "FIRST")]
    n_econ = min(len(economy), min_economy)
    picked = premium[: top - n_econ] + economy[: top - min(len(premium), top - n_econ)]
    return sorted(picked, key=lambda s: -s.surplus)[:top]


def _load_digest() -> dict:
    try:
        with open(DIGEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _email_dict(d: dict) -> dict:
    extra = []
    if d["direct"]:
        extra.append("nonstop")
    if d["airlines"]:
        extra.append(d["airlines"])
    if d["other_dates"]:
        extra.append(f"+{len(d['other_dates'])} more date(s): {', '.join(d['other_dates'][:6])}")
    if d["alternatives"]:
        extra.append("also " + "; ".join(d["alternatives"]))
    note = (f"${d['surplus_usd']:,.0f} above the {d['great_floor']:.1f}¢ bar · {d['seats']} seat(s) · vs {d['cash_basis']}"
            + (f" (award airline's own fare ${d['same_carrier_cash']:,.0f})" if d.get("same_carrier_cash") else "")
            + (" · valued against the business fare" if d["cabin"] == "FIRST" else "")
            + (" · cash fare from a date within 7 days" if d["cash_is_approx"] else ""))
    return {**d, "flight_number": " · ".join(extra) or None, "note": note}


def run(max_lookups: int, top: int, send_email: bool, include_planned: bool = False, log=print) -> dict:
    flight_search.SERPAPI_MAX_CALLS = SERPAPI_CAP
    started = datetime.now(timezone.utc)
    cands, scan_stats = award_scanner.scan(include_planned=include_planned)
    log(f"Scan: {scan_stats['candidates']:,} fresh awards from {scan_stats['calls']} seats.aero calls "
        f"({scan_stats['stale']:,} stale rows dropped; {scan_stats['rate_limit_remaining']} calls left today)")

    model = fare_model.FareModel()
    log(f"Fare model: {model.summary()}")
    scored = score_candidates(cands, model)
    promising = sum(1 for s in scored if s.p_great >= MIN_P_GREAT_TO_PRICE)
    log(f"Estimates: {promising:,} candidates have >= {MIN_P_GREAT_TO_PRICE:.0%} chance of clearing the great bar")

    price_stats = price_promising(scored, max_lookups, log=log)
    log(f"Pricing: {price_stats}")

    ranked = shortlist(scored, top)
    digest = _load_digest()
    reported: dict = digest.get("reported", {})
    cutoff = (started - timedelta(days=REPORT_COOLDOWN_DAYS)).isoformat()
    reported = {k: v for k, v in reported.items() if v >= cutoff}
    top_dicts = [s.to_dict() for s in ranked]
    fresh = [d for s, d in zip(ranked, top_dicts) if s.report_key not in reported]
    for s, d in zip(ranked, top_dicts):
        d["new"] = s.report_key not in reported

    emailed = False
    if send_email and fresh and deal_email.is_configured():
        try:
            deal_email.send_deal_alert_email(
                [_email_dict(d) for d in fresh],
                subject=f"Deal Finder: {len(fresh)} new standout award(s), best "
                        f"${fresh[0]['surplus_usd']:,.0f} above the bar ({fresh[0]['origin']}->{fresh[0]['dest']})",
                intro=f"Ranked from {scan_stats['candidates']:,} award options by dollars saved above your "
                      f"cabin's great-deal bar. Cash fares are live Google Flights prices.",
            )
            emailed = True
            for s in ranked:
                reported.setdefault(s.report_key, started.isoformat())
        except Exception as e:
            log(f"Email failed: {e}")

    out = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan": {k: v for k, v in scan_stats.items() if k != "sources"} | {"sources": scan_stats["sources"]},
        "model": model.summary(),
        "pricing": price_stats,
        "top": top_dicts,
        "emailed_new": len(fresh) if emailed else 0,
        "reported": reported,
    }
    with open(DIGEST_PATH, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-lookups", type=int, default=DEFAULT_MAX_LOOKUPS)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--include-planned", action="store_true",
                    help="also scan programs reachable only from cards you plan to get")
    args = ap.parse_args(argv)
    out = run(args.max_lookups, args.top, not args.no_email, args.include_planned)
    print(f"\nTop {len(out['top'])} (new: {sum(d['new'] for d in out['top'])}):")
    for i, d in enumerate(out["top"], 1):
        more = f" +{len(d['other_dates'])}d" if d["other_dates"] else ""
        print(f"{i:>2}. {d['origin']}-{d['dest']} {d['cabin'][:4]} {d['program'][:24]:24} {d['date']}{more:10} "
              f"{d['points']:>7,} + ${d['taxes_usd']:.0f} vs ${d['cash_price']:,.0f}"
              f"{'~' if d['cash_is_approx'] else ' '} {d['cpp']:.2f}¢  +${d['surplus_usd']:,.0f}"
              f"{'  NEW' if d['new'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
