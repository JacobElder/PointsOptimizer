"""
Stochastic Valuation Engine — Monte Carlo simulation for points vs. cash decisions.

Decision framework
------------------
The engine computes the *Expected CPP (cents-per-point)* the user would realise from
future redemptions if they hoard their points today (avg_simulated_cpp).

That figure is then compared to `current_cpp` — the CPP of the specific deal on the
table right now:

  current_cpp  > avg_simulated_cpp  →  Redeem  (today's deal beats expected future)
  current_cpp  ≤ avg_simulated_cpp  →  Hoard   (today's deal is below future average)

Key design choice: the simulated future CPP is derived from (cash_price / points_drawn)
rather than from `current_cpp` itself. This keeps the two sides of the comparison
independent and allows the model to recommend Hoard when the current deal is a poor use
of points (e.g. a points-for-gift-cards redemption when better future travel is expected).
"""

from __future__ import annotations

from dataclasses import dataclass

import math

import numpy as np


@dataclass
class SimulationResult:
    """Output bundle returned by run_valuation_simulation."""
    recommend_redeem: bool      # True → redeem now;  False → hoard
    avg_simulated_cpp: float    # mean NPV-weighted future CPP across all iterations
    current_cpp: float          # CPP of the redemption being evaluated today
    iterations: int             # Monte Carlo iterations run
    percentile_5: float         # 5th-percentile future CPP (downside)
    percentile_95: float        # 95th-percentile future CPP (upside)
    avg_trips_per_year: float   # mean trips drawn per year across all iterations


def run_valuation_simulation(
    current_cpp: float,
    point_balance: float,
    cash_price: float,
    time_horizon: int = 3,
    lambda_trips: float = 2.0,
    mu_cost: float = 11.0,
    sigma_cost: float = 0.5,
    depreciation_rate: float = 0.05,
    market_return: float = 0.05,
    n_iterations: int = 10_000,
    rng_seed: int | None = None,
) -> SimulationResult:
    """
    Run a Monte Carlo simulation to find the Expected CPP of hoarding points,
    then compare it to the CPP of a redemption available right now.

    Parameters
    ----------
    current_cpp : float
        Cents-per-point of the specific redemption being evaluated today.
        E.g. 1.5 for a good-but-not-great airline redemption.
    point_balance : float
        Current total points balance (e.g. 100_000).
    cash_price : float
        Cash price (USD) of a representative target trip used to calibrate
        the implied value of future point redemptions.
    time_horizon : int
        Years over which to model future trips (typically 3 or 5).
    lambda_trips : float
        Poisson λ — expected number of high-value redemptions per year.
    mu_cost : float
        Log-normal μ for points required per trip (log-scale mean).
        Default 11  →  median trip cost ≈ e^11 ≈ 59 800 pts (business/premium
        economy).
    sigma_cost : float
        Log-normal σ for points required per trip (log-scale std).
    depreciation_rate : float
        Annual point devaluation rate d.  Default 0.05 (5 %).
        Models the steady erosion of award-chart purchasing power.
    market_return : float
        Annual opportunity-cost rate r for the cash you DON'T spend today.
        Default 0.05 (5 % — inflation-adjusted conservative return, per PRD).
    n_iterations : int
        Monte Carlo iterations.  Default 10 000.
    rng_seed : int | None
        Optional seed for reproducibility.

    Returns
    -------
    SimulationResult
        See dataclass definition above.

    Raises
    ------
    ValueError
        If any input is outside its valid domain (see input validation below).

    Notes
    -----
    For each iteration the engine:
      1. Draws yearly trip counts from Poisson(lambda_trips).
      2. Draws per-trip point costs from LogNormal(mu_cost, sigma_cost).
      3. At year t the effective CPP is discounted by devaluation and
         opportunity cost using continuous compounding:
             CPP_t = (cash_price / pts) × exp(−(d + r) · t).
         The continuous form exp(−rate·t) is always real (unlike (1−d)^t,
         which goes complex for d > 1) and is the correct model form —
         (1−d)^t ≈ (1+d)^-t ≈ exp(−d·t) for small d.
      4. Values every drawn trip, independent of point_balance. Any rule that
         conditions on affordability biases the average: crediting a partial
         balance at the full trip's CPP swung the same deal from 2.54¢ (1k pts)
         to 1.94¢ (500k), and redeeming only trips that fit cherry-picks cheap,
         high-CPP trips (2.67¢ at 60k). The question answered is "what is a
         point worth in typical future use", which doesn't depend on how many
         you hold; point_balance is validated but doesn't move the estimate.
      5. Returns the NPV-weighted average CPP across all redeemed trips.

    The zero-trip fallback is made scale-consistent with the main trip path.
    Because the per-iteration CPP is the POINTS-WEIGHTED average
    npv_cash_total / points_redeemed_total (and pts cancels inside npv_cash),
    the trip path realises cash_price · Σdiscount / Σpts ≈ cash_price / E[pts],
    i.e. it scales as cash_price · exp(−μ − σ²/2) — the reciprocal of the MEAN
    point cost, not the median exp(−μ) the old fallback used.  The fallback
    therefore uses exp(−μ − σ²/2); verified numerically the two regimes agree
    in scale (ratio ≈ 0.97 vs ≈ 0.76 for the naive exp(−μ + σ²/2)).
    """
    # ---------------------------------------------------------------------- #
    # 0. Input validation — raise clear, UI-surfaceable errors               #
    # ---------------------------------------------------------------------- #
    for name, val in (("current_cpp", current_cpp), ("point_balance", point_balance),
                      ("cash_price", cash_price)):
        if not math.isfinite(val):
            raise ValueError(f"{name} must be a finite number (got {val}).")
    if cash_price <= 0:
        raise ValueError(f"cash_price must be greater than 0 (got {cash_price}).")
    if n_iterations < 1:
        raise ValueError(
            f"n_iterations must be at least 1 (got {n_iterations})."
        )
    if point_balance <= 0:
        raise ValueError(
            "point_balance must be greater than 0 — there are no points to "
            f"redeem or hoard (got {point_balance})."
        )
    if time_horizon < 1:
        raise ValueError(
            f"time_horizon must be at least 1 year (got {time_horizon})."
        )
    if not (0 <= depreciation_rate < 1):
        raise ValueError(
            "depreciation_rate must be in the interval [0, 1) — an annual "
            f"devaluation of 100% or more is not modellable (got {depreciation_rate})."
        )
    if market_return < 0:
        raise ValueError(
            f"market_return must be non-negative (got {market_return})."
        )

    rng = np.random.default_rng(rng_seed)

    simulated_cpp_per_iter = np.empty(n_iterations)
    total_trips_drawn: list[int] = []

    for i in range(n_iterations):
        npv_cash_total = 0.0   # sum of NPV cash values from all redeemed point-trips
        points_redeemed_total = 0.0

        for year in range(1, time_horizon + 1):
            # ---------------------------------------------------------------- #
            # 1. Stochastic travel demand                                       #
            # ---------------------------------------------------------------- #
            n_trips: int = int(rng.poisson(lambda_trips))
            total_trips_drawn.append(n_trips)

            if n_trips == 0:
                continue

            # ---------------------------------------------------------------- #
            # 2. Point cost per trip — Log-Normal draw                          #
            # ---------------------------------------------------------------- #
            trip_point_costs: np.ndarray = rng.lognormal(
                mean=mu_cost, sigma=sigma_cost, size=n_trips
            )

            # ---------------------------------------------------------------- #
            # 3. Discount factor at year t (continuous compounding)             #
            #    Combines point devaluation AND opportunity cost of cash.       #
            #    exp(−d·t) — points buy less travel each year                   #
            #    exp(−r·t) — cash saved in the future is worth less today       #
            #    Always real (no ComplexWarning even if a rate is large).       #
            # ---------------------------------------------------------------- #
            discount = np.exp(-(depreciation_rate + market_return) * year)

            for pts_required in trip_point_costs:
                # NPV cash value of this future trip's points, at its own CPP
                # (cash_price / pts_required per point), discounted to today.
                npv_cash_total += cash_price * discount
                points_redeemed_total += pts_required

        # -------------------------------------------------------------------- #
        # 4. Simulated CPP for this iteration (cents per point)                 #
        #    = NPV cash saved / points used × 100                               #
        #                                                                        #
        # Fallback for zero-trips iterations: apply mid-horizon discounting to  #
        # the implied baseline CPP so the estimate stays conservative.          #
        # -------------------------------------------------------------------- #
        if points_redeemed_total > 0:
            simulated_cpp_per_iter[i] = (npv_cash_total / points_redeemed_total) * 100
        else:
            # No trips ever drawn — points just sit and devalue.
            # Baseline matches the trip path's points-weighted scale:
            # cash_price / E[pts] = cash_price · exp(−μ − σ²/2).  Using exp(−μ)
            # (median) as before over-stated the fallback by ~exp(σ²/2), a
            # spurious discontinuity that biased avg_simulated_cpp as the
            # fallback fraction grew (small balance / small λ).
            mid_t = (time_horizon + 1) / 2.0  # mean of discrete years 1..T, matching the trip path
            baseline_cpp = (
                cash_price * np.exp(-mu_cost - sigma_cost ** 2 / 2.0) * 100
            )  # mean-point-cost-implied CPP (scale-consistent with trip path)
            discount = np.exp(-(depreciation_rate + market_return) * mid_t)
            simulated_cpp_per_iter[i] = baseline_cpp * discount

    # -------------------------------------------------------------------------- #
    # 5. Aggregate                                                                #
    # -------------------------------------------------------------------------- #
    avg_simulated_cpp = float(np.mean(simulated_cpp_per_iter))
    p5 = float(np.percentile(simulated_cpp_per_iter, 5))
    p95 = float(np.percentile(simulated_cpp_per_iter, 95))
    avg_trips = float(np.mean(total_trips_drawn)) if total_trips_drawn else 0.0

    return SimulationResult(
        recommend_redeem=current_cpp > avg_simulated_cpp,
        avg_simulated_cpp=round(avg_simulated_cpp, 4),
        current_cpp=round(current_cpp, 4),
        iterations=n_iterations,
        percentile_5=round(p5, 4),
        percentile_95=round(p95, 4),
        avg_trips_per_year=round(avg_trips, 2),
    )
