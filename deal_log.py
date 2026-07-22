"""
Persistent, git-tracked log of seats.aero alert-email deals, split into two
stages: `pending` (captured from Gmail, not yet priced) and `deals` (priced,
with real CPP). The split exists because the cloud routine that can reach
Gmail cannot reach SerpApi (org egress policy blocks serpapi.com from that
sandbox) -- see TODO.md item 4. So the cloud routine only ever appends to
`pending`; a local script (price_pending_deals.py) run via a Mac LaunchAgent
does the actual pricing and moves entries into `deals`.

Deliberately git-tracked (unlike ledger.py's gitignored balances.json/
history.csv) so both the cloud routine's fresh checkout and the local
machine's checkout share the same queue, and so pages/5_Deal_Radar.py can
display whatever was last pushed from either side.
"""

from __future__ import annotations

import json
import os

_BASE = os.path.dirname(os.path.abspath(__file__))
DEAL_LOG_PATH = os.path.join(_BASE, "deal_log.json")

_DEFAULT_KEYS = ("processed_message_ids", "deals", "pending")


def load() -> dict:
    if not os.path.exists(DEAL_LOG_PATH):
        data = {}
    else:
        try:
            with open(DEAL_LOG_PATH) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    for key in _DEFAULT_KEYS:
        data.setdefault(key, [])
    return data


def save(data: dict) -> None:
    with open(DEAL_LOG_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def make_key(program: str, origin: str, destination: str, cabin: str, date: str, points: int) -> str:
    return "|".join([program.strip().lower(), origin.strip().upper(), destination.strip().upper(),
                      cabin.strip().upper(), date.strip(), str(int(points))])


def _key_of(d: dict) -> str:
    return make_key(d["program"], d["origin"], d["dest"], d["cabin"], d["date"], d["points"])


def existing_keys(data: dict) -> set[str]:
    """Keys already captured, whether priced (deals) or still queued (pending)."""
    return {_key_of(d) for d in data["deals"]} | {_key_of(d) for d in data["pending"]}


# "Great deal" bar for notifications/highlighting -- higher for Business/First since
# committing a much larger points balance to one seat warrants more proof it's a
# standout, vs. Economy where less is at stake per redemption. Premium Economy is
# bucketed with Economy. Confirmed with the user 2026-07-22.
GREAT_CPP_BY_CABIN = {
    "ECONOMY": 1.5,
    "PREMIUM_ECONOMY": 1.5,
    "BUSINESS": 2.0,
    "FIRST": 2.0,
}


def is_great(deal: dict) -> bool:
    cpp = deal.get("cpp")
    if cpp is None:
        return False
    floor = GREAT_CPP_BY_CABIN.get(deal.get("cabin", "").upper(), 2.0)
    return cpp >= floor
