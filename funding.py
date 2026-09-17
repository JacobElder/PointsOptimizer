"""How to pay for an award with the points and miles you have."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

import ledger
from cards_data import POOLS, pool_is_active, rank_funding_pools, transfer_ratio_multiplier

BONUSES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transfer_bonuses.json")


def active_bonus(pool_key: str, partner: str, on: date | None = None) -> dict | None:
    """The active transfer bonus for pool -> partner, if any (expired entries ignored)."""
    on = on or date.today()
    try:
        with open(BONUSES_PATH) as f:
            bonuses = json.load(f).get("bonuses", [])
    except (OSError, json.JSONDecodeError):
        return None
    for b in bonuses:
        if b.get("pool") == pool_key and b.get("partner") == partner and str(on) <= str(b.get("ends", "")):
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
            return f"Pay with the {self.held_miles:,} {self.program} miles you already have"
        prefix = f"Use your {self.held_miles:,} {self.program} miles, then " if self.held_miles else ""
        if not self.pools:
            if self.held_miles:
                return f"You have {self.held_miles:,} {self.program} miles but none of your cards can top them up"
            return f"None of your points transfer to {self.program}"
        best = self.pools[0]
        verb = "transfer" if prefix else "Transfer"
        bonus = best.get("bonus")
        line = (f"{prefix}{verb} {best['pts_needed']:,.0f} {best['pool'].currency_name}"
                + (f" (+{bonus['bonus_pct']}% transfer bonus until {date.fromisoformat(bonus['ends']):%b %-d})"
                   if bonus else f" ({best['partner'].ratio})"))
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
            r["pts_needed"] = top_up / (transfer_ratio_multiplier(r["partner"].ratio) * (1 + bonus["bonus_pct"] / 100))
            r["covered"] = r["balance"] >= r["pts_needed"]
    pools.sort(key=lambda r: (not r["covered"], r["flexibility"], r["pts_needed"]))
    return PayPlan(program, int(points), held, top_up, pools)
