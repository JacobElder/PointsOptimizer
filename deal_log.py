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
import re

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
    """Keys already captured, whether priced (deals) or still queued (pending).

    Tolerant of malformed entries -- a bad row (missing/None field) is skipped
    rather than crashing the whole capture run.
    """
    keys: set[str] = set()
    for d in list(data.get("deals", [])) + list(data.get("pending", [])):
        try:
            keys.add(_key_of(d))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return keys


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED_PENDING_FIELDS = ("origin", "dest", "program", "cabin", "date", "points", "taxes")


def is_valid_pending(entry: dict) -> bool:
    """Whether a queued pending entry is well-formed enough to price.

    The pending queue is produced by an out-of-repo LLM parsing Gmail, so
    entries are not guaranteed well-formed. price_pending_deals validates each
    entry with this before pricing so one bad row can't crash the whole run.
    """
    if not isinstance(entry, dict):
        return False
    for field in _REQUIRED_PENDING_FIELDS:
        if entry.get(field) in (None, ""):
            return False
    if not isinstance(entry["date"], str) or not _DATE_RE.match(entry["date"]):
        return False
    try:
        if int(entry["points"]) <= 0:
            return False
        float(entry["taxes"])
    except (TypeError, ValueError):
        return False
    return True


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


# Below this cents-per-point a deal is a clear SKIP regardless of cabin.
SKIP_CPP = 1.0


def great_floor(cabin: str) -> float:
    """The cabin-aware CPP bar at/above which a deal is a standout ("great")."""
    return GREAT_CPP_BY_CABIN.get((cabin or "").upper(), 2.0)


def verdict_for(cpp: float | None, cabin: str) -> str:
    """Single source of truth for a deal's verdict.

    BOOK is defined as clearing the same cabin-aware bar `is_great` uses, so the
    emailed "standout" set and the on-page BOOK badge can never contradict each
    other (previously the emailer used cabin-aware floors while the badge used a
    flat 1.7c, so an economy deal at 1.6c was emailed yet shown "BORDERLINE").
    """
    if cpp is None:
        return "NO CASH PRICE"
    if cpp >= great_floor(cabin):
        return "BOOK"
    if cpp < SKIP_CPP:
        return "SKIP"
    return "BORDERLINE"


def is_great(deal: dict) -> bool:
    cpp = deal.get("cpp")
    if cpp is None:
        return False
    return cpp >= great_floor(deal.get("cabin", ""))
