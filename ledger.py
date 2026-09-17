"""
Local persistence for point balances.

Both files live next to the code and are gitignored — they're personal data.
- balances.json: {pool_key: point_balance}
- program_balances.json: {partner program name: miles already transferred into it}
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
BALANCES_PATH = os.path.join(_BASE, "balances.json")
PROGRAM_BALANCES_PATH = os.path.join(_BASE, "program_balances.json")



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


def load_program_balances() -> dict[str, int]:
    """Miles already sitting in airline/hotel programs, keyed by cards_data Partner.name.

    Read from program_balances.json (local, gitignored), overridden by the
    PROGRAM_BALANCES environment variable (a JSON object) where set, so the
    GitHub Actions run can get them from a repo secret without publishing them.
    """
    data: dict = {}
    try:
        with open(PROGRAM_BALANCES_PATH) as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except (OSError, json.JSONDecodeError):
        pass
    env = os.environ.get("PROGRAM_BALANCES")
    if env:
        try:
            loaded = json.loads(env)
            if isinstance(loaded, dict):
                data.update(loaded)
        except json.JSONDecodeError:
            logger.warning("PROGRAM_BALANCES is not valid JSON; ignoring it")
    out: dict[str, int] = {}
    for k, v in data.items():
        try:
            if int(float(v)) > 0:
                out[str(k)] = int(float(v))
        except (TypeError, ValueError):
            continue
    return out


def save_program_balances(balances: dict[str, int]) -> None:
    with open(PROGRAM_BALANCES_PATH, "w") as f:
        json.dump({k: int(v) for k, v in balances.items() if int(v) > 0}, f, indent=2, sort_keys=True)
        f.write("\n")


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
