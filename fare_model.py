"""
Cash-fare estimator: predicts a one-way cash fare (with uncertainty) for any
route/cabin, so award candidates can be ranked by *estimated* CPP before any
live price lookup is spent on them.

Model (fit on every real fare we've recorded -- cash_quotes.json + priced deals
in deal_log.json -- so each lookup improves it):

    log(price) = b0 + b1*log(great-circle miles) + cabin effect + region-pair effect
                 + region-pair x premium-cabin effect (business costs ~2.6x economy
                   to Latin America but ~3.9x to Europe in the recorded data)
                 + route effect (shrunk toward 0: n/(n+K) of the route's mean residual)

Uncertainty is measured, not assumed: sigma for an unseen route comes from
leave-one-route-out residuals; for a route with n recorded fares it narrows
toward the within-route (date-to-date) spread. Backtest on deal_log.json
(2026-09-16): distance+cabin+region was ~14% median error on unseen routes,
and a nearby-date quote on the same route ~1-5%.

This is a triage tool for choosing what to price, never a final CPP.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

import cash_quotes
import deal_log

_BASE = os.path.dirname(os.path.abspath(__file__))
_COORDS_PATH = os.path.join(_BASE, "airport_coords.json")

ROUTE_SHRINK_K = 2.0
RIDGE = 0.5
# Cabins with little or no training data borrow a neighbour's coefficient plus a
# conservative multiplier and extra uncertainty.
_CABIN_FALLBACK = {"FIRST": ("BUSINESS", math.log(1.6)), "PREMIUM_ECONOMY": ("ECONOMY", math.log(1.8))}
_MIN_CABIN_ROWS = 5

_coords: dict | None = None


def _load_coords() -> dict:
    global _coords
    if _coords is None:
        with open(_COORDS_PATH) as f:
            _coords = json.load(f)
    return _coords


def distance_miles(origin: str, dest: str) -> float | None:
    c = _load_coords()
    a, b = c.get(origin.upper()), c.get(dest.upper())
    if not a or not b:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def region_pair(origin: str, dest: str) -> str:
    c = _load_coords()
    ra = (c.get(origin.upper()) or [0, 0, "?"])[2]
    rb = (c.get(dest.upper()) or [0, 0, "?"])[2]
    return "|".join(sorted((ra, rb)))


@dataclass
class Estimate:
    median_price: float
    sigma_log: float
    route_obs: int  # recorded fares on this exact route+cabin
    basis: str  # "route" (has route data) or "model" (distance/region only)

    def low_high(self, z: float = 1.0) -> tuple[float, float]:
        return (self.median_price * math.exp(-z * self.sigma_log), self.median_price * math.exp(z * self.sigma_log))

    def prob_at_least(self, price: float) -> float:
        """P(actual fare >= price) under a lognormal around the estimate."""
        if price <= 0:
            return 1.0
        zscore = (math.log(price) - math.log(self.median_price)) / self.sigma_log
        return 0.5 * math.erfc(zscore / math.sqrt(2))


def training_rows() -> list[dict]:
    """Real observed fares, one per route/cabin/date (newest observation wins)."""
    rows: dict[tuple, dict] = {}
    for d in deal_log.load().get("deals", []):
        if d.get("cash_price") and not d.get("cash_is_approx"):
            key = (d["origin"], d["dest"], d["cabin"], d.get("cash_quote_date") or d["date"])
            rows[key] = {"origin": d["origin"], "dest": d["dest"], "cabin": d["cabin"],
                         "price": float(d["cash_price"]), "at": d.get("checked_at", "")}
    for q in cash_quotes.load():
        if q.get("price_usd"):
            key = (q["origin"], q["dest"], q["cabin"], q["date"])
            prev = rows.get(key)
            if prev is None or q["fetched_at"] >= prev["at"]:
                rows[key] = {"origin": q["origin"], "dest": q["dest"], "cabin": q["cabin"],
                             "price": float(q["price_usd"]), "at": q["fetched_at"]}
    return [r for r in rows.values() if distance_miles(r["origin"], r["dest"])]


class FareModel:
    def __init__(self, rows: list[dict] | None = None):
        rows = training_rows() if rows is None else rows
        self.rows = rows
        self.cabins = sorted({r["cabin"] for r in rows
                              if sum(1 for x in rows if x["cabin"] == r["cabin"]) >= _MIN_CABIN_ROWS})
        self.regions = sorted({region_pair(r["origin"], r["dest"]) for r in rows})
        if len(rows) < 10 or not self.cabins:
            raise ValueError(f"Not enough fare data to fit ({len(rows)} rows)")
        self.base_cabin = "ECONOMY" if "ECONOMY" in self.cabins else self.cabins[0]
        self.beta = self._fit(rows)
        resid = self._residuals(rows, self.beta)
        self.route_resid: dict[tuple, list[float]] = {}
        for r, e in zip(rows, resid):
            self.route_resid.setdefault((r["origin"], r["dest"], r["cabin"]), []).append(e)
        self.sigma_unseen = self._cv_sigma(rows)
        within = [e - np.mean(v) for v in self.route_resid.values() if len(v) > 1 for e in v]
        self.sigma_within = max(float(np.std(within)) if len(within) > 5 else 0.15, 0.05)

    # ── design matrix ───────────────────────────────────────────────────────
    def _cabin_col(self, cabin: str) -> tuple[str, float]:
        if cabin in self.cabins:
            return cabin, 0.0
        base, bump = _CABIN_FALLBACK.get(cabin, (self.base_cabin, 0.0))
        return (base if base in self.cabins else self.base_cabin), bump

    def _x(self, origin: str, dest: str, cabin: str) -> tuple[np.ndarray, float]:
        dist = distance_miles(origin, dest) or 1000.0
        cab, bump = self._cabin_col(cabin)
        x = [1.0, math.log(dist)]
        x += [1.0 if cab == c else 0.0 for c in self.cabins if c != self.base_cabin]
        rp = region_pair(origin, dest)
        premium = 1.0 if cab in ("BUSINESS", "FIRST") else 0.0
        x += [1.0 if rp == g else 0.0 for g in self.regions]
        x += [premium if rp == g else 0.0 for g in self.regions]
        return np.array(x), bump

    def _fit(self, rows: list[dict]) -> np.ndarray:
        X = np.array([self._x(r["origin"], r["dest"], r["cabin"])[0] for r in rows])
        y = np.log([r["price"] for r in rows])
        penalty = RIDGE * np.eye(X.shape[1])
        penalty[0, 0] = penalty[1, 1] = 0.0  # don't shrink intercept / distance slope
        return np.linalg.solve(X.T @ X + penalty, X.T @ y)

    def _residuals(self, rows: list[dict], beta: np.ndarray) -> np.ndarray:
        X = np.array([self._x(r["origin"], r["dest"], r["cabin"])[0] for r in rows])
        return np.log([r["price"] for r in rows]) - X @ beta

    def _cv_sigma(self, rows: list[dict]) -> float:
        routes = sorted({(r["origin"], r["dest"]) for r in rows})
        errs = []
        for rt in routes:
            train = [r for r in rows if (r["origin"], r["dest"]) != rt]
            test = [r for r in rows if (r["origin"], r["dest"]) == rt]
            if len(train) < 10:
                continue
            beta = self._fit(train)
            errs.extend(self._residuals(test, beta).tolist())
        return float(np.sqrt(np.mean(np.square(errs)))) if errs else 0.35

    # ── prediction ──────────────────────────────────────────────────────────
    def estimate(self, origin: str, dest: str, cabin: str) -> Estimate:
        origin, dest, cabin = origin.upper(), dest.upper(), cabin.upper()
        x, bump = self._x(origin, dest, cabin)
        mu = float(x @ self.beta) + bump
        sigma = self.sigma_unseen + (0.15 if bump else 0.0)
        resid = self.route_resid.get((origin, dest, cabin), [])
        n = len(resid)
        if n:
            w = n / (n + ROUTE_SHRINK_K)
            mu += w * float(np.mean(resid))
            sigma = math.sqrt(self.sigma_within ** 2 + (sigma ** 2) * (1 - w))
        return Estimate(median_price=math.exp(mu), sigma_log=sigma, route_obs=n,
                        basis="route" if n else "model")

    def summary(self) -> str:
        return (f"{len(self.rows)} fares, {len({(r['origin'], r['dest']) for r in self.rows})} routes; "
                f"sigma unseen route {self.sigma_unseen:.2f}, same route {self.sigma_within:.2f} "
                f"(fit {datetime.now(timezone.utc):%Y-%m-%d})")
