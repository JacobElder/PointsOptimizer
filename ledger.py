"""
Local persistence for per-pool point balances and analysis history.

Both files live next to the code and are gitignored — they're personal data.
- balances.json: {pool_key: point_balance}
- history.csv: one row per analyzed flight (see HISTORY_COLUMNS)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date

_BASE = os.path.dirname(os.path.abspath(__file__))
BALANCES_PATH = os.path.join(_BASE, "balances.json")
HISTORY_PATH = os.path.join(_BASE, "history.csv")

HISTORY_COLUMNS = [
    "date",
    "route",
    "program",
    "pool_key",
    "cash_price",
    "taxes_fees",
    "points_required",
    "cpp",
    "avg_simulated_cpp",
    "verdict",
]


# ── Balances ───────────────────────────────────────────────────────────────
def load_balances() -> dict[str, int]:
    if not os.path.exists(BALANCES_PATH):
        return {}
    try:
        with open(BALANCES_PATH) as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}


def save_balances(balances: dict[str, int]) -> None:
    with open(BALANCES_PATH, "w") as f:
        json.dump(balances, f, indent=2)


# ── History ────────────────────────────────────────────────────────────────
def append_history(
    route: str,
    program: str,
    pool_key: str,
    cash_price: float,
    taxes_fees: float,
    points_required: int,
    cpp: float,
    avg_simulated_cpp: float,
    verdict: str,
) -> None:
    exists = os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "date": date.today().isoformat(),
                "route": route,
                "program": program,
                "pool_key": pool_key,
                "cash_price": round(cash_price, 2),
                "taxes_fees": round(taxes_fees, 2),
                "points_required": points_required,
                "cpp": round(cpp, 4),
                "avg_simulated_cpp": round(avg_simulated_cpp, 4),
                "verdict": verdict,
            }
        )


def load_history() -> list[dict]:
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, newline="") as f:
        return list(csv.DictReader(f))
