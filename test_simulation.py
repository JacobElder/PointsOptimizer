"""Smoke tests for the Stochastic Valuation Engine."""

import pytest
from simulation import run_valuation_simulation, SimulationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
COMMON = dict(
    point_balance=100_000,
    cash_price=1_500,   # $1 500 reference trip
    mu_cost=11.0,       # median ~60k pts → baseline CPP ≈ 2.5 cpp
    sigma_cost=0.5,
    rng_seed=42,
)


# ---------------------------------------------------------------------------
# Return-type tests
# ---------------------------------------------------------------------------

def test_returns_simulation_result():
    result = run_valuation_simulation(current_cpp=1.5, **COMMON)
    assert isinstance(result, SimulationResult)


def test_result_fields_are_finite():
    result = run_valuation_simulation(current_cpp=1.5, **COMMON)
    for field in (
        result.avg_simulated_cpp,
        result.percentile_5,
        result.percentile_95,
        result.avg_trips_per_year,
    ):
        assert field == field           # not NaN
        assert abs(field) < 1e9        # not Inf


# ---------------------------------------------------------------------------
# Decision-logic tests
#
# baseline_cpp ≈ cash_price / exp(mu_cost) * 100
#              = 1500 / exp(11) * 100 ≈ 2.51 cpp
# After 3 years of 5 % devaluation + 7 % opportunity cost, the discounted
# avg_simulated_cpp is roughly 2.51 × 0.79 ≈ 1.98 cpp.
# ---------------------------------------------------------------------------

def test_recommend_redeem_when_current_cpp_clearly_above_simulated():
    # 10 cpp  ≫ ~2.0 cpp simulated → must Redeem
    result = run_valuation_simulation(current_cpp=10.0, **COMMON)
    assert result.recommend_redeem is True
    assert result.current_cpp > result.avg_simulated_cpp


def test_recommend_hoard_when_current_cpp_clearly_below_simulated():
    # 0.3 cpp  ≪ ~2.0 cpp simulated → must Hoard
    result = run_valuation_simulation(current_cpp=0.3, **COMMON)
    assert result.recommend_redeem is False
    assert result.current_cpp < result.avg_simulated_cpp


def test_simulated_cpp_positive():
    result = run_valuation_simulation(current_cpp=1.5, **COMMON)
    assert result.avg_simulated_cpp > 0


# ---------------------------------------------------------------------------
# Statistical sanity tests
# ---------------------------------------------------------------------------

def test_percentiles_ordered():
    result = run_valuation_simulation(current_cpp=1.5, **COMMON)
    assert result.percentile_5 <= result.avg_simulated_cpp <= result.percentile_95


def test_avg_trips_per_year_near_lambda():
    """Mean drawn trips should converge to lambda_trips (LLN)."""
    result = run_valuation_simulation(
        current_cpp=1.5, lambda_trips=3.0, n_iterations=10_000, **COMMON
    )
    assert abs(result.avg_trips_per_year - 3.0) < 0.1


# ---------------------------------------------------------------------------
# Parameter-behaviour tests
# ---------------------------------------------------------------------------

def test_iteration_count_respected():
    result = run_valuation_simulation(current_cpp=1.5, n_iterations=500, **COMMON)
    assert result.iterations == 500


def test_reproducibility():
    kwargs = dict(current_cpp=1.8, **COMMON)
    r1 = run_valuation_simulation(**kwargs)
    r2 = run_valuation_simulation(**kwargs)
    assert r1.avg_simulated_cpp == r2.avg_simulated_cpp


def test_higher_depreciation_lowers_simulated_cpp():
    """More devaluation → lower future CPP → easier to exceed with today's deal."""
    low_dep = run_valuation_simulation(current_cpp=1.5, depreciation_rate=0.01, **COMMON)
    high_dep = run_valuation_simulation(current_cpp=1.5, depreciation_rate=0.20, **COMMON)
    assert high_dep.avg_simulated_cpp < low_dep.avg_simulated_cpp


def test_exhausted_balance_handled_gracefully():
    """Tiny balance runs out fast; engine should not raise and should return valid CPP."""
    result = run_valuation_simulation(
        current_cpp=1.5,
        point_balance=1_000,    # exhausted after first trip
        cash_price=1_500,
        lambda_trips=5,
        rng_seed=42,
    )
    assert isinstance(result.avg_simulated_cpp, float)
    assert result.avg_simulated_cpp > 0


def test_zero_lambda_trips_fallback():
    """No trips ever drawn → fallback to mid-horizon devaluation estimate."""
    result = run_valuation_simulation(
        current_cpp=1.5, lambda_trips=0.0, n_iterations=200, **COMMON
    )
    assert result.avg_simulated_cpp > 0
    assert result.avg_trips_per_year == 0.0


# ---------------------------------------------------------------------------
# FIX 5a — regime consistency: the fallback-heavy and trip-heavy regimes must
# agree in scale.  The old fallback used exp(-mu) (median), which over-stated
# the fallback by ~exp(sigma^2/2) relative to the points-weighted trip path,
# biasing avg_simulated_cpp purely by how many iterations hit the fallback.
# ---------------------------------------------------------------------------

def test_fallback_and_trip_regimes_agree_in_scale():
    common = dict(
        current_cpp=1.5,
        cash_price=1_500,
        mu_cost=11.0,
        sigma_cost=0.5,
        depreciation_rate=0.05,
        market_return=0.05,
        time_horizon=3,
        n_iterations=20_000,
        rng_seed=1,
    )
    # Trip-heavy: enormous balance + high lambda → points never exhaust, almost
    # every iteration realises real trips.
    trip_heavy = run_valuation_simulation(
        point_balance=10_000_000_000, lambda_trips=5.0, **common
    )
    # Fallback-heavy: lambda ~0 → almost every iteration hits the zero-trip path.
    fallback_heavy = run_valuation_simulation(
        point_balance=100_000, lambda_trips=1e-6, **common
    )
    ratio = trip_heavy.avg_simulated_cpp / fallback_heavy.avg_simulated_cpp
    # Same economic scale: within ~10%.  (The pre-fix code sat near ~0.76,
    # and exp(-mu+sigma^2/2) would also fail this.)
    assert 0.90 < ratio < 1.10, (
        f"regime discontinuity: trip={trip_heavy.avg_simulated_cpp} "
        f"fallback={fallback_heavy.avg_simulated_cpp} ratio={ratio:.3f}"
    )


# ---------------------------------------------------------------------------
# FIX 5b — continuous-compounding discount is always real; depreciation_rate
# near 1 must not yield complex/negative-zero nonsense.  (depreciation_rate is
# now validated to < 1, so test the largest still-valid value.)
# ---------------------------------------------------------------------------

def test_high_depreciation_stays_real_and_nonnegative():
    result = run_valuation_simulation(
        current_cpp=1.5,
        depreciation_rate=0.999,   # extreme but valid; base was negative pre-fix
        lambda_trips=0.0,          # force the fallback path (site of the old bug)
        n_iterations=200,
        **COMMON,
    )
    for field in (result.avg_simulated_cpp, result.percentile_5, result.percentile_95):
        assert isinstance(field, float)
        assert field == field                 # not NaN
        assert field >= 0.0                    # no negative-zero / complex-cast junk
        # negative zero would compare == 0.0 but carry a sign bit
        import math
        assert not (field == 0.0 and math.copysign(1.0, field) < 0)


# ---------------------------------------------------------------------------
# FIX 5c — input validation
# ---------------------------------------------------------------------------

def test_invalid_n_iterations_raises():
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, n_iterations=0, **COMMON)


def test_invalid_point_balance_raises():
    kwargs = {k: v for k, v in COMMON.items() if k != "point_balance"}
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, point_balance=0, **kwargs)
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, point_balance=-100, **kwargs)


def test_invalid_time_horizon_raises():
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, time_horizon=0, **COMMON)


def test_invalid_depreciation_rate_raises():
    # >= 1 is invalid (the old complex-number regime)
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, depreciation_rate=1.0, **COMMON)
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, depreciation_rate=1.5, **COMMON)
    # negative is invalid too
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, depreciation_rate=-0.01, **COMMON)


def test_invalid_market_return_raises():
    with pytest.raises(ValueError):
        run_valuation_simulation(current_cpp=1.5, market_return=-0.01, **COMMON)


# ---------------------------------------------------------------------------
# Parameter monotonicity — higher opportunity cost (market_return) discounts
# future point value more, so avg_simulated_cpp must fall.
# ---------------------------------------------------------------------------

def test_higher_market_return_lowers_simulated_cpp():
    low_r = run_valuation_simulation(current_cpp=1.5, market_return=0.02, **COMMON)
    high_r = run_valuation_simulation(current_cpp=1.5, market_return=0.15, **COMMON)
    assert high_r.avg_simulated_cpp < low_r.avg_simulated_cpp


# ---------------------------------------------------------------------------
# Decision boundary — the comparison is strict (current_cpp > avg → redeem),
# so exactly at the boundary the engine must recommend HOARD, not redeem.
# ---------------------------------------------------------------------------

def test_decision_boundary_is_hoard_when_equal():
    # Use the zero-trip (all-fallback) path: it is a closed form with NO Monte
    # Carlo noise, so every iteration yields the SAME value and avg equals it
    # exactly.  Recompute that exact float and feed it back as current_cpp so
    # the unrounded decision comparison sees a true tie (no rounding drift).
    import numpy as np
    cash_price = 1_500
    mu_cost = 11.0
    sigma_cost = 0.5
    depreciation_rate = 0.05
    market_return = 0.05
    time_horizon = 3
    mid_t = time_horizon / 2.0
    baseline_cpp = cash_price * np.exp(-mu_cost - sigma_cost ** 2 / 2.0) * 100
    discount = np.exp(-(depreciation_rate + market_return) * mid_t)
    boundary = float(baseline_cpp * discount)

    result = run_valuation_simulation(
        current_cpp=boundary,
        point_balance=100_000,
        cash_price=cash_price,
        mu_cost=mu_cost,
        sigma_cost=sigma_cost,
        depreciation_rate=depreciation_rate,
        market_return=market_return,
        time_horizon=time_horizon,
        lambda_trips=0.0,        # force the deterministic fallback everywhere
        n_iterations=1,          # mean of one element is bit-exact → true tie
        rng_seed=42,
    )
    # Fallback is deterministic → percentiles collapse onto the mean.
    assert result.percentile_5 == result.avg_simulated_cpp == result.percentile_95
    # current_cpp == avg_simulated_cpp exactly → strict `>` is False → HOARD.
    assert result.recommend_redeem is False
