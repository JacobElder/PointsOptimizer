"""
Local persistence for per-pool point balances and analysis history.

Both files live next to the code and are gitignored — they're personal data.
- balances.json: {pool_key: point_balance}
- history.csv: one row per analyzed flight (see HISTORY_COLUMNS)
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from datetime import date

logger = logging.getLogger(__name__)

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
    except (json.JSONDecodeError, OSError):
        # File is genuinely absent or wholesale-corrupt (unparseable JSON).
        return {}
    if not isinstance(data, dict):
        # Valid JSON but not an object (e.g. a list or scalar) — unusable.
        return {}
    # Coerce per-key so one bad value doesn't discard every good balance.
    result: dict[str, int] = {}
    for k, v in data.items():
        try:
            # int(float(v)) tolerates "12000", 12000.0, etc.; truncates toward
            # zero (a fractional point balance isn't meaningful anyway).
            result[str(k)] = int(float(v))
        except (TypeError, ValueError):
            logger.warning("Skipping unparseable balance for %r: %r", k, v)
    return result


def save_balances(balances: dict[str, int]) -> None:
    # Atomic write: dump to a temp file in the SAME directory, then os.replace.
    # os.replace is atomic on POSIX, so a crash mid-write can never truncate or
    # corrupt the existing balances.json — readers see either the old or the
    # new file, never a half-written one.
    directory = os.path.dirname(BALANCES_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".balances-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(balances, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, BALANCES_PATH)
    except BaseException:
        # Clean up the temp file on any failure so we don't leave litter behind;
        # the pre-existing balances.json is untouched because replace never ran.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    # Write the header when the file is missing OR exists but is empty (size 0);
    # an existing-but-empty file would otherwise get headerless rows.
    needs_header = (
        not os.path.exists(HISTORY_PATH) or os.path.getsize(HISTORY_PATH) == 0
    )
    with open(HISTORY_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        if needs_header:
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
