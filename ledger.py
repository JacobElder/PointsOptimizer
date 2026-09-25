"""
Point balances: card pools ({pool_key: points}) and miles already in airline or
hotel programs ({program name: miles}).

Storage:
- With a GIST_TOKEN (GitHub classic token with only the "gist" scope, set as an
  environment variable or Streamlit secret), balances live in ONE secret Gist
  (pointsoptimizer_balances.json). The Wallet page on your Mac or the hosted site
  saves there, and the daily GitHub run reads it, so there's one place to update.
  The Gist is created on first use from any local balances.
- Without it, local gitignored files: balances.json and program_balances.json
  (plus the PROGRAM_BALANCES env var for the GitHub run).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
BALANCES_PATH = os.path.join(_BASE, "balances.json")
PROGRAM_BALANCES_PATH = os.path.join(_BASE, "program_balances.json")

GIST_FILENAME = "pointsoptimizer_balances.json"
GIST_DISABLED = False  # tests set this
_gist_id: str | None = None
_gist_unavailable = False  # a read failed this process: refuse to overwrite the Gist
_gist_seen_at: str | None = None  # updated_at we last read, to spot a concurrent write


# ── Gist storage ──────────────────────────────────────────────────────────
def _gist_token() -> str | None:
    if GIST_DISABLED:
        return None
    token = os.environ.get("GIST_TOKEN")
    if not token:
        try:
            import streamlit as st

            token = st.secrets.get("GIST_TOKEN")
        except Exception:
            token = None
    return token or None


def gist_enabled() -> bool:
    return _gist_token() is not None


def _gh(method: str, path: str, **kw):
    resp = requests.request(method, f"https://api.github.com{path}", timeout=15,
                            headers={"Authorization": f"Bearer {_gist_token()}",
                                     "Accept": "application/vnd.github+json"}, **kw)
    resp.raise_for_status()
    return resp.json()


def _find_gist_id() -> str | None:
    global _gist_id
    if _gist_id:
        return _gist_id
    for page in range(1, 51):  # page to exhaustion: a miss here reads as "no gist yet"
        gists = _gh("GET", "/gists", params={"per_page": 100, "page": page})
        for g in gists:
            if GIST_FILENAME in (g.get("files") or {}):
                _gist_id = g["id"]
                return _gist_id
        if len(gists) < 100:
            break
    return None


def _gist_write(data: dict) -> None:
    global _gist_id
    body = {"files": {GIST_FILENAME: {"content": json.dumps(data, indent=2, sort_keys=True)}}}
    gid = _find_gist_id()
    if gid:
        _gh("PATCH", f"/gists/{gid}", json=body)
    else:
        created = _gh("POST", "/gists", json={**body, "public": False,
                                              "description": "PointsOptimizer balances"})
        _gist_id = created["id"]


def gist_failed() -> bool:
    """A Gist read failed in this process, so local files may be an empty stand-in."""
    return _gist_unavailable


def _gist_read() -> dict | None:
    """Balances from the Gist; creates it from local files the first time. None on any failure."""
    global _gist_unavailable, _gist_seen_at
    try:
        gid = _find_gist_id()
        if gid is None:
            cards, programs = _load_local_balances(), _load_local_program_balances()
            if not cards and not programs:
                # Nothing to seed it with. The daily CI runner has no local files, so
                # creating here would publish an EMPTY gist; GitHub lists newest first,
                # the app would then find that one instead of the real balances, and
                # the next save would PATCH the empty copy.
                logger.warning("No balances gist found and no local balances to create one from; "
                               "using local files")
                _gist_unavailable = True
                return None
            data = {"cards": cards, "programs": programs,
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            _gist_write(data)
            return data
        gist = _gh("GET", f"/gists/{gid}")
        data = json.loads(gist["files"][GIST_FILENAME]["content"])
        _gist_unavailable = False
        _gist_seen_at = gist.get("updated_at")
        return data
    except Exception as e:  # network, auth, malformed content: fall back to local
        logger.warning("Gist balances unavailable (%s); using local files", type(e).__name__)
        _gist_unavailable = True
        return None


def _gist_update(part: str, values: dict[str, int]) -> bool:
    seen_before = _gist_seen_at
    current = _gist_read()
    if current is None:
        return False
    if seen_before and _gist_seen_at and _gist_seen_at != seen_before:
        # Someone else (the site, the daily run) wrote since we loaded: don't
        # overwrite their change with a page that was rendered from older data.
        logger.warning("Balances Gist changed since it was read; not overwriting")
        return False
    current[part] = values
    current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        _gist_write(current)
        return True
    except Exception as e:
        logger.warning("Couldn't save balances to Gist (%s)", type(e).__name__)
        return False


def _coerce(data) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (data or {}).items():
        try:
            out[str(k)] = int(float(v))
        except (TypeError, ValueError):
            continue
    return out



# ── Balances ───────────────────────────────────────────────────────────────
def load_balances() -> dict[str, int]:
    if gist_enabled():
        data = _gist_read()
        if data is not None:
            return _coerce(data.get("cards"))
    return _load_local_balances()


def _load_local_balances() -> dict[str, int]:
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
    if gist_enabled():
        data = _gist_read()
        if data is not None:
            return {k: v for k, v in _coerce(data.get("programs")).items() if v > 0}
    return _load_local_program_balances()


def _load_local_program_balances() -> dict[str, int]:
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


def save_program_balances(balances: dict[str, int]) -> bool:
    """Saves locally, and to the Gist when configured. Returns True if the Gist was updated.

    If the Gist is configured but unreadable, nothing is written at all: the values
    on screen may be an empty local stand-in, and saving them would destroy the real ones.
    """
    if gist_enabled() and gist_failed():
        logger.warning("Refusing to save balances: the Gist could not be read")
        return False
    balances = {k: int(v) for k, v in balances.items() if int(v) > 0}
    synced = gist_enabled() and _gist_update("programs", balances)
    with open(PROGRAM_BALANCES_PATH, "w") as f:
        json.dump(balances, f, indent=2, sort_keys=True)
        f.write("\n")
    return bool(synced)


def save_balances(balances: dict[str, int]) -> bool:
    """Saves locally, and to the Gist when configured. Returns True if the Gist was updated.

    Refuses entirely when the Gist is configured but unreadable (see save_program_balances).
    """
    if gist_enabled() and gist_failed():
        logger.warning("Refusing to save balances: the Gist could not be read")
        return False
    synced = gist_enabled() and _gist_update("cards", {k: int(v) for k, v in balances.items()})
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
    return bool(synced)
