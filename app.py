"""PointsOptimizer entrypoint: page navigation. Each page lives in views/."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(page_title="PointsOptimizer", page_icon="✈️", layout="centered")

pages = [
    st.Page("views/top_deals.py", title="Top Deals", icon="🏆", default=True),
    st.Page("views/flight_analyzer.py", title="Flight Search", icon="✈️"),
    st.Page("views/wallet.py", title="Wallet", icon="💳"),
    st.Page("views/roadmap.py", title="Card Roadmap", icon="🗺️"),
    st.Page("views/captured_alerts.py", title="Captured Alerts", icon="📬"),
]
st.navigation(pages).run()
