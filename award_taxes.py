"""Estimated award taxes for programs seats.aero reports as $0.

seats.aero's TotalTaxesRaw comes back 0 for some programs -- Turkish Miles&Smiles
among them -- and 0 is never literally true: every award carries at least US
departure tax, and 0 of 337 real bookings in deal_log.json carried $0. Treating a
missing value as zero inflates CPP, always in the deal's favour: a 65,000-point
Turkish business award to Istanbul reads 3.73c at $0 and 3.40c at the ~$219 that
route actually charges.

Estimates come from what this account's own scans observe, per program and cabin,
with a small curated table for programs that never report a figure at all. An
estimate is always labelled as one on the card.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.abspath(__file__))
TAXES_PATH = os.path.join(_BASE, "program_taxes.json")

MIN_OBSERVATIONS = 5  # below this a median is noise, so the curated figure wins
STALE_DAYS = 120


def _key(source: str, cabin: str) -> str:
    return f"{source}|{(cabin or '').upper()}"


def load() -> dict:
    try:
        with open(TAXES_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"learned": {}, "curated": {}}


def save(data: dict) -> None:
    """Atomic: a crash mid-write would leave unparseable JSON and lose every
    observation this account has accumulated."""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TAXES_PATH) or ".", prefix=".taxes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, TAXES_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def learn(candidates, data: dict | None = None) -> dict:
    """Update per-program/cabin medians from the awards that DO report taxes.

    Self-maintaining: surcharges change, and each scan sees tens of thousands of
    awards, so the table tracks what this account actually encounters rather than
    a number someone typed in once.
    """
    data = data if data is not None else load()
    seen: dict[str, list[float]] = {}
    for c in candidates:
        usd = getattr(c, "taxes_usd_observed", None)
        if usd is None:
            usd = _observed_usd(c)
        if usd and usd > 0:
            seen.setdefault(_key(c.source, c.cabin), []).append(usd)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for k, values in seen.items():
        if len(values) >= MIN_OBSERVATIONS:
            data.setdefault("learned", {})[k] = {
                "usd": round(statistics.median(values), 2), "n": len(values), "updated": now}
    return data


def _observed_usd(c) -> float | None:
    try:
        import valuation
        return float(c.taxes) * valuation.fx_rate(c.taxes_currency)
    except Exception:  # noqa: BLE001 - a bad row must not stop the scan
        return None


def estimate(source: str, cabin: str, data: dict | None = None) -> tuple[float | None, str]:
    """(usd, how we got it). (None, reason) when there's nothing to go on."""
    data = data if data is not None else load()
    k = _key(source, cabin)

    learned = (data.get("learned") or {}).get(k)
    if learned and learned.get("usd"):
        return float(learned["usd"]), f"typical for {source} in this cabin ({learned['n']} awards seen)"

    curated = (data.get("curated") or {}).get(k)
    if curated and curated.get("usd"):
        return float(curated["usd"]), curated.get("note") or "published figure for this program"

    # Same program, any cabin: taxes track the route and carrier more than the cabin.
    for table in ("learned", "curated"):
        for key, row in (data.get(table) or {}).items():
            if key.split("|")[0] == source and row.get("usd"):
                return float(row["usd"]), f"typical for {source}"
    return None, "no estimate available"
