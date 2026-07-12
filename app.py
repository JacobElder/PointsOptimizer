import streamlit as st
from simulation import run_valuation_simulation

st.set_page_config(page_title="PointsOptimizer", page_icon="✈️", layout="centered")

st.title("PointsOptimizer")
st.subheader("Should you redeem your points today or hoard them?")

st.divider()

# ── Inputs ──────────────────────────────────────────────────────────────────
st.header("Your Redemption")

col1, col2 = st.columns(2)
with col1:
    cash_price = st.number_input("Cash price of trip ($)", min_value=50.0, value=1500.0, step=50.0)
    points_required = st.number_input("Points required for this trip", min_value=1000, value=60000, step=1000)
with col2:
    point_balance = st.number_input("Your current points balance", min_value=1000, value=100000, step=1000)

current_cpp = (cash_price / points_required) * 100
st.metric("Current CPP of this redemption", f"{current_cpp:.2f}¢")

st.divider()
st.header("Simulation Settings")

col3, col4 = st.columns(2)
with col3:
    lambda_trips = st.slider("Expected high-value trips per year (λ)", 0.5, 10.0, 2.0, 0.5)
    time_horizon = st.selectbox("Time horizon (years)", [3, 5], index=0)
    depreciation_rate = st.slider("Annual point devaluation rate", 0.01, 0.20, 0.05, 0.01, format="%.0f%%",
                                   help="How much purchasing power points lose each year.")
with col4:
    mu_cost = st.slider("Avg log-cost of future trips (μ)", 9.0, 13.0, 11.0, 0.5,
                         help="Log-scale mean of the point cost distribution. e^11 ≈ 60k pts.")
    sigma_cost = st.slider("Variability of future trip costs (σ)", 0.1, 1.5, 0.5, 0.1)
    market_return = st.slider("Annual opportunity cost of cash", 0.01, 0.15, 0.07, 0.01, format="%.0f%%",
                               help="Expected annual return if cash were invested instead.")

st.divider()

# ── Run ──────────────────────────────────────────────────────────────────────
if st.button("Run Simulation", type="primary", use_container_width=True):
    with st.spinner("Running 10,000 iterations..."):
        result = run_valuation_simulation(
            current_cpp=current_cpp,
            point_balance=point_balance,
            cash_price=cash_price,
            time_horizon=time_horizon,
            lambda_trips=lambda_trips,
            mu_cost=mu_cost,
            sigma_cost=sigma_cost,
            depreciation_rate=depreciation_rate,
            market_return=market_return,
        )

    # ── Verdict ──────────────────────────────────────────────────────────────
    if result.recommend_redeem:
        st.success("## Recommendation: REDEEM NOW")
        st.write(
            f"Today's deal (**{result.current_cpp:.2f}¢/pt**) beats the simulated "
            f"future average of **{result.avg_simulated_cpp:.2f}¢/pt**. Lock it in."
        )
    else:
        st.warning("## Recommendation: HOARD")
        st.write(
            f"Today's deal (**{result.current_cpp:.2f}¢/pt**) is below the simulated "
            f"future average of **{result.avg_simulated_cpp:.2f}¢/pt**. Wait for a better redemption."
        )

    st.divider()

    # ── Stats ─────────────────────────────────────────────────────────────────
    st.header("Simulation Results")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Today's CPP", f"{result.current_cpp:.2f}¢")
    c2.metric("Avg Simulated CPP", f"{result.avg_simulated_cpp:.2f}¢")
    c3.metric("5th Percentile", f"{result.percentile_5:.2f}¢")
    c4.metric("95th Percentile", f"{result.percentile_95:.2f}¢")

    st.caption(
        f"Based on {result.iterations:,} Monte Carlo iterations · "
        f"Avg {result.avg_trips_per_year:.1f} trips/year drawn · "
        f"{time_horizon}-year horizon"
    )
