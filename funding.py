"""How to pay for an award with the points and miles you have."""

from __future__ import annotations

from dataclasses import dataclass, field

import ledger
from cards_data import POOLS, pool_is_active, rank_funding_pools


@dataclass
class PayPlan:
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
        line = (f"{prefix}{verb} {best['pts_needed']:,.0f} {best['pool'].currency_name}"
                f" ({best['partner'].ratio})")
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
    return PayPlan(program, int(points), held, top_up, pools)
