"""
Daily record of the cheapest award per program/route/cabin/travel-month.

Ranking by dollars-above-baseline mostly measures deal size (it correlates 0.92
with how many points a deal costs). "Cheapest this route has been in 90 days"
measures whether a price is unusual, needs no cash fare at all, and works beyond
the ~11-month window Google will price. The scan already has the data, so this
costs no API calls.

Storage (award_history.json): {"YYYY-MM-DD": {key: points}}, recording only
values that CHANGED since the previous day — award prices are mostly static, so
full daily snapshots would add megabytes a week for no information.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

_BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(_BASE, "award_history.json")
KEEP_DAYS = 180
MIN_DAYS_FOR_PERCENTILE = 10  # below this there's no distribution worth quoting
WINDOW_DAYS = 90


def key_for(source: str, origin: str, dest: str, cabin: str, travel_date: str) -> str:
    return f"{source}|{origin}|{dest}|{cabin}|{travel_date[:7]}"


def load(path: str = "") -> dict:
    try:
        with open(path or HISTORY_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def series(hist: dict, window_days: int = WINDOW_DAYS) -> dict[str, list[int]]:
    """Each key's price on each day in the window, carrying values forward."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=window_days)).isoformat()
    current: dict[str, int] = {}
    out: dict[str, list[int]] = {}
    for day in sorted(hist):
        current.update(hist[day])
        if day < cutoff:
            continue
        for k, v in current.items():
            out.setdefault(k, []).append(v)
    return out


def percentile(hist_series: dict[str, list[int]], key: str, points: int) -> tuple[float | None, int]:
    """(share of recorded days this price beats, days of history).

    1.0 means the cheapest this route/cabin/month has been in the window.
    """
    values = hist_series.get(key) or []
    if len(values) < MIN_DAYS_FOR_PERCENTILE:
        return None, len(values)
    beaten = sum(1 for v in values if points < v)
    same = sum(1 for v in values if points == v)
    return (beaten + same / 2) / len(values), len(values)


def record(cands, path: str = "") -> dict:
    """Add today's cheapest price per key, storing only what changed."""
    path = path or HISTORY_PATH
    hist = load(path)
    today = datetime.now(timezone.utc).date().isoformat()
    cheapest: dict[str, int] = dict(hist.get(today, {}))
    for c in cands:
        k = key_for(c.source, c.origin, c.dest, c.cabin, c.date)
        if k not in cheapest or c.points < cheapest[k]:
            cheapest[k] = c.points
    previous: dict[str, int] = {}
    for day in sorted(d for d in hist if d < today):
        previous.update(hist[day])
    hist[today] = {k: v for k, v in cheapest.items() if previous.get(k) != v}
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=KEEP_DAYS)).isoformat()
    hist = {d: v for d, v in hist.items() if d >= cutoff}
    _write_atomic(path, hist)
    return hist


def _write_atomic(path: str, data: dict) -> None:
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".hist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"), sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
