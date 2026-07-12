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
