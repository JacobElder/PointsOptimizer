import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import streamlit as st

import ledger
from cards_data import POOLS

st.set_page_config(page_title="History — PointsOptimizer", page_icon="📊", layout="centered")

st.title("Analysis History")
st.caption(
    "Every flight you've run through the analyzer, logged locally to history.csv. "
    "Once you have 10+ entries, the calibration section suggests simulation parameters "
    "fitted from your own behavior instead of hand-tuned guesses."
)

rows = ledger.load_history()

if not rows:
    st.info("No analyses logged yet. Run a simulation on the Flight Analyzer page and it will appear here.")
    st.stop()

# ── Table ──────────────────────────────────────────────────────────────────
st.header(f"Logged analyses ({len(rows)})")

display_rows = [
    {
        "Date": r["date"],
        "Route": r["route"] or "—",
        "Program": r["program"] or "—",
        "Pool": POOLS[r["pool_key"]].currency_name if r["pool_key"] in POOLS else "—",
        "Cash $": f"{float(r['cash_price']):,.0f}",
        "Points": f"{int(r['points_required']):,}",
        "CPP ¢": f"{float(r['cpp']):.2f}",
        "Sim avg ¢": f"{float(r['avg_simulated_cpp']):.2f}",
        "Verdict": r["verdict"].upper(),
    }
    for r in reversed(rows)
]
st.dataframe(display_rows, use_container_width=True)

# ── Summary stats ──────────────────────────────────────────────────────────
cpps = np.array([float(r["cpp"]) for r in rows])
points = np.array([int(r["points_required"]) for r in rows])
redeems = [r for r in rows if r["verdict"] == "redeem"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Analyses", len(rows))
c2.metric("Redeem verdicts", len(redeems))
c3.metric("Median CPP seen", f"{np.median(cpps):.2f}¢")
c4.metric("Best CPP seen", f"{cpps.max():.2f}¢")

st.divider()

# ── Calibration ────────────────────────────────────────────────────────────
st.header("Calibrate the simulation from your history")

MIN_SAMPLES = 10
if len(rows) < MIN_SAMPLES:
    st.info(
        f"Need at least {MIN_SAMPLES} logged analyses to fit parameters — you have {len(rows)}. "
        "Keep analyzing flights and this will unlock."
    )
else:
    # Log-normal fit for point costs: mu/sigma are the mean/std of log(points).
    log_pts = np.log(points)
    fitted_mu = float(np.mean(log_pts))
    fitted_sigma = float(np.std(log_pts, ddof=1))

    # Trips per year: redeem-verdict analyses per year of logged history.
    dates = sorted(r["date"] for r in rows)
    from datetime import date

    span_days = max((date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days, 30)
    fitted_lambda = len(redeems) / (span_days / 365.25)

    st.write(
        "Fitted from your logged analyses — use these in the simulation settings on the "
        "Flight Analyzer instead of the defaults:"
    )
    f1, f2, f3 = st.columns(3)
    f1.metric("μ (avg log point-cost)", f"{fitted_mu:.2f}", help=f"e^μ ≈ {np.exp(fitted_mu):,.0f} points/trip")
    f2.metric("σ (variability)", f"{fitted_sigma:.2f}")
    f3.metric(
        "λ (trips/year)",
        f"{fitted_lambda:.1f}",
        help="Redeem verdicts per year of logged history. Treat as a floor — it only counts "
        "flights you analyzed here.",
    )
    st.caption(
        f"Based on {len(rows)} analyses spanning {span_days} days. "
        "The λ estimate assumes redeem verdicts approximate actual bookings."
    )
