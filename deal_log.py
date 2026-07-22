"""
Persistent, git-tracked log of seats.aero alert-email deals that have been
evaluated for real CPP, plus which alert-email message IDs have already been
processed. Deliberately tracked in git (unlike ledger.py's balances.json/
history.csv) so a scheduled cloud routine's fresh checkout on each run can
pick up where the last run left off, and so pages/5_Deal_Radar.py can display
whatever the routine last pushed.
"""

from __future__ import annotations

import json
import os

_BASE = os.path.dirname(os.path.abspath(__file__))
DEAL_LOG_PATH = os.path.join(_BASE, "deal_log.json")

_DEFAULT = {"processed_message_ids": [], "deals": []}


def load() -> dict:
    if not os.path.exists(DEAL_LOG_PATH):
        return {"processed_message_ids": [], "deals": []}
    try:
        with open(DEAL_LOG_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"processed_message_ids": [], "deals": []}
    data.setdefault("processed_message_ids", [])
    data.setdefault("deals", [])
    return data


def save(data: dict) -> None:
    with open(DEAL_LOG_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def make_key(program: str, origin: str, destination: str, cabin: str, date: str, points: int) -> str:
    return "|".join([program.strip().lower(), origin.strip().upper(), destination.strip().upper(),
                      cabin.strip().upper(), date.strip(), str(int(points))])


def existing_keys(data: dict) -> set[str]:
    return {
        make_key(d["program"], d["origin"], d["destination"], d["cabin"], d["date"], d["points"])
        for d in data["deals"]
    }
