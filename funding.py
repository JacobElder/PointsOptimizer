"""How to pay for an award with the points and miles you have."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

import ledger
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
                + (f" (+{bonus['bonus_pct']}% transfer bonus until {date.fromisoformat(bonus['ends']):%b %-d})"
                   if bonus else f" ({partner.ratio_on()}{soon})"))
        if best["balance"]:
            line += f": you have {best['balance']:,}" + ("" if best["covered"] else ", not enough")
        others = [r for r in self.pools[1:] if r["covered"] or not best["covered"]]
        if others:
            line += " · or " + ", ".join(r["pool"].currency_name for r in others)
        return line


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
