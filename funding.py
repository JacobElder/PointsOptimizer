"""How to pay for an award with the points and miles you have."""

from __future__ import annotations

import json
import os
import statistics
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import date

import ledger
import valuation
from cards_data import POOLS, pool_is_active, rank_funding_pools, transfer_ratio_multiplier

BONUSES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transfer_bonuses.json")


TRANSFER_INCREMENT = 1000  # issuers move points in 1,000-point blocks


def _round_up(points: float) -> int:
    return int(-(-points // TRANSFER_INCREMENT) * TRANSFER_INCREMENT)


def active_bonus(pool_key: str, partner: str, on: date | None = None) -> dict | None:
    """The active transfer bonus for pool -> partner, if any (expired entries ignored)."""
    on = on or date.today()
    try:
        with open(BONUSES_PATH) as f:
            bonuses = json.load(f).get("bonuses", [])
    except (OSError, json.JSONDecodeError):
        return None
    for b in bonuses:
        if (b.get("pool") == pool_key and b.get("partner") == partner
                and str(b.get("starts", "")) <= str(on) <= str(b.get("ends", ""))):
            return b
    return None


@dataclass
class PayPlan:
    """How to pay; pools rows may carry "bonus" (the active transfer bonus applied)."""
    program: str
    points: int
    held_miles: int = 0
    top_up: int = 0  # points still needed after using miles already held
    pools: list[dict] = field(default_factory=list)  # rank_funding_pools rows for active pools, best first

    @property
    def covered_by_held(self) -> bool:
        return self.held_miles >= self.points

    @property
    def summary(self) -> str:
        if self.covered_by_held:
            return (f"Pay with {self.points:,} of the {self.held_miles:,} {self.program} miles "
                    "you already have")
        prefix = f"Use your {self.held_miles:,} {self.program} miles, then " if self.held_miles else ""
        if not self.pools:
            if self.held_miles:
                return f"You have {self.held_miles:,} {self.program} miles but none of your cards can top them up"
            return f"None of your points transfer to {self.program}"
        best = self.pools[0]
        verb = "transfer" if prefix else "Transfer"
        bonus = best.get("bonus")
        partner = best["partner"]
        soon = (f", before it drops to {partner.ratio_after} on "
                f"{date.fromisoformat(partner.changes_on):%b %-d}"
                if partner.changes_on and partner.ratio_after
                and partner.ratio_on() == partner.ratio else "")
        line = (f"{prefix}{verb} {_round_up(best['pts_needed']):,} {best['pool'].currency_name}"
                + (f" ({_bonus_phrase(bonus, self.top_up, partner)})"
                   if bonus else f" ({partner.ratio_on()}{soon})"))
        if best["balance"]:
            line += f": you have {best['balance']:,}" + ("" if best["covered"] else ", not enough")
        others = [r for r in self.pools[1:] if r["covered"] or not best["covered"]]
        if others:
            line += " · or " + ", ".join(r["pool"].currency_name for r in others)
        return line


BONUS_ENDING_DAYS = 3  # a bonus ending within this many days says so, with the cost without it
BONUS_LIST_STALE_DAYS = 21  # transfer_bonuses.json not re-checked in this long is flagged


def _bonus_phrase(bonus: dict, top_up: int, partner, on: date | None = None) -> str:
    """"+20% transfer bonus until Sep 30", plus the deadline and what it costs after
    when it ends within BONUS_ENDING_DAYS: the bonus is the reason this program won,
    and a transfer made a day late needs ~20% more points."""
    ends = date.fromisoformat(bonus["ends"])
    phrase = f"+{bonus['bonus_pct']}% transfer bonus until {ends:%b %-d}"
    left = (ends - (on or date.today())).days
    if 0 <= left <= BONUS_ENDING_DAYS:
        when = "ends today" if left == 0 else f"{left} day{'s' if left > 1 else ''} left"
        without = _round_up(top_up / transfer_ratio_multiplier(partner.ratio_on()))
        phrase += f" — {when}; {without:,} without it"
    return phrase


def bonus_list_health(on: date | None = None) -> str | None:
    """A note when transfer_bonuses.json hasn't been checked in BONUS_LIST_STALE_DAYS.

    Expired entries are ignored silently, so a list nobody revisits quietly drops to
    standard ratios everywhere, and the program choice (which counts bonuses) stops
    reflecting bonuses running now. Staleness is measured from the file's `checked`
    date or the newest entry's `verified`, whichever is later: an empty list checked
    last week is accurate (after Oct 15 none of your cards has a bonus), so emptiness
    alone is not a reason to nag.
    """
    on = on or date.today()
    try:
        with open(BONUSES_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "The transfer bonus list (transfer_bonuses.json) couldn't be read."
    dates = [str(data.get("checked", ""))] + [str(b.get("verified", "")) for b in data.get("bonuses", [])]
    dates = [d for d in dates if d]
    if not dates:
        return ("The transfer bonus list has never been checked; add any running bonuses to "
                "transfer_bonuses.json and set its `checked` date.")
    age = (on - date.fromisoformat(max(dates))).days
    if age > BONUS_LIST_STALE_DAYS:
        usable = [b for b in data.get("bonuses", []) if b.get("pool") in POOLS
                  and pool_is_active(b["pool"]) and str(b.get("ends", "")) >= str(on)]
        return (f"The transfer bonus list was last checked {age} days ago"
                + ("" if usable else " and lists nothing running for your cards")
                + "; new bonuses may be missing from transfer_bonuses.json.")
    return None


def plan(program: str, points: int, balances: dict[str, int] | None = None,
         program_balances: dict[str, int] | None = None) -> PayPlan:
    balances = ledger.load_balances() if balances is None else balances
    program_balances = ledger.load_program_balances() if program_balances is None else program_balances
    held = int(program_balances.get(program, 0))
    top_up = max(int(points) - held, 0)
    matches = [(pool, pt) for pool in POOLS.values() for pt in pool.partners
               if pt.name == program and pool.transferable and pool_is_active(pool.key)]
    pools = rank_funding_pools(matches, top_up, balances) if top_up else []
    for r in pools:  # apply active transfer bonuses: fewer card points needed
        bonus = active_bonus(r["pool"].key, program)
        if bonus:
            r["bonus"] = bonus
            r["pts_needed"] = top_up / (transfer_ratio_multiplier(r["partner"].ratio_on())
                                        * (1 + bonus["bonus_pct"] / 100))
    for r in pools:
        # "Covered" has to answer the question the instruction asks: transfers move
        # in 1,000-point increments, so a balance can clear pts_needed and still not
        # cover the transfer the summary tells you to make.
        r["covered"] = r["balance"] >= _round_up(r["pts_needed"])
    # A bonus is the whole point of spending one pool over another -- a 70% bonus can
    # save 20,000 points on a single award -- so it outranks the "keep the flexible
    # pool" tiebreak instead of sitting below it.
    pools.sort(key=lambda r: (not r["covered"], not r.get("bonus"), r["flexibility"], r["pts_needed"]))
    return PayPlan(program, int(points), held, top_up, pools)


@lru_cache(maxsize=None)
def pool_point_cents(pool_key: str, cabin: str) -> float:
    """What one card point is normally worth when moved to an airline, in cents.

    The median of the pool's airline partners' baselines for the cabin (Chase UR:
    1.3 economy, ~2.0 business). Needed to compare awards in different programs
    funded from the same card: valuing each at its own airline's baseline made an
    83,000-point United transfer look better than a 63,000-point Aeroplan one for
    the same flight, only because United miles are "normally worth" less.
    """
    names = [pt.name for pt in POOLS[pool_key].partners if pt.kind == "airline"]
    if not names:
        return valuation.baseline_cpp("", cabin)
    return statistics.median(valuation.baseline_cpp(n, cabin) for n in names)


@lru_cache(maxsize=4096)
def _cheapest_transfer(program: str, top_up: int, on: date) -> tuple[str, float] | None:
    best = None
    for pool in POOLS.values():
        if not (pool.transferable and pool_is_active(pool.key)):
            continue
        for pt in pool.partners:
            if pt.name != program:
                continue
            bonus = active_bonus(pool.key, program, on)
            needed = top_up / (transfer_ratio_multiplier(pt.ratio_on(on))
                               * (1 + (bonus["bonus_pct"] / 100 if bonus else 0)))
            if best is None or needed < best[1]:
                best = (pool.key, needed)
    return best


def opportunity_cost_usd(program: str, cabin: str, points: int, held_miles: int = 0,
                         on: date | None = None) -> float:
    """Dollar value of what you'd actually give up to book this award.

    Miles already in the program are valued at that program's baseline. The rest
    comes from the card pool needing the fewest points (transfer bonuses applied),
    valued at what that card's points are normally worth -- one yardstick for every
    program the card reaches. Programs no card reaches fall back to their own baseline.
    """
    held = min(int(held_miles), int(points))
    top_up = int(points) - held
    cost = held * valuation.baseline_cpp(program, cabin) / 100
    if top_up:
        best = _cheapest_transfer(program, top_up, on or date.today())
        if best:
            cost += _round_up(best[1]) * pool_point_cents(best[0], cabin) / 100
        else:
            cost += top_up * valuation.baseline_cpp(program, cabin) / 100
    return cost
