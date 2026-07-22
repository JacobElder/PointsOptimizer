import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import deal_log

st.set_page_config(page_title="Deal Radar — PointsOptimizer", page_icon="📡", layout="centered")

st.title("Deal Radar")
st.caption(
    "A scheduled cloud routine checks your seats.aero alert emails every few hours, computes real "
    "CPP against a live cash price, and logs every deal it evaluates here. Most alerts clear "
    "seats.aero's own points/fee threshold but aren't actually good value — this is the needle-in-"
    "the-haystack view."
)

data = deal_log.load()
deals = data.get("deals", [])

if not deals:
    st.info(
        "No deals logged yet. Once the scheduled routine runs, evaluated deals will show up here "
        "automatically — just refresh this page."
    )
    st.stop()

deals_sorted = sorted(deals, key=lambda d: d.get("cpp") or 0, reverse=True)
last_checked = max((d.get("checked_at", "") for d in deals), default="")

st.caption(f"{len(deals)} deals evaluated · last update {last_checked}")

great = [d for d in deals_sorted if deal_log.is_great(d)]
if great:
    st.header(f"🎯 {len(great)} standout deal(s)")
    for d in great:
        with st.container(border=True):
            st.markdown(
                f"### {d['origin']}→{d['dest']} · {d['program']} · {d['cabin'].title()} — "
                f"**{d['cpp']:.2f}¢/pt**"
            )
            st.write(
                f"{d['points']:,} pts + ${d['taxes']:.2f} {d.get('currency', 'USD')} taxes "
                f"vs. ${d['cash_price']:,.0f} cash · travel {d['date']}"
            )
            if d.get("flight_number"):
                st.caption(f"Flight {d['flight_number']}")
            if d.get("listing_url"):
                st.markdown(f"[View on seats.aero]({d['listing_url']})")
    st.divider()

st.header("All evaluated deals")
for d in deals_sorted:
    badge = {"BOOK": "🟢", "BORDERLINE": "🟡", "SKIP": "🔴"}.get(d.get("verdict"), "⚪")
    with st.container(border=True):
        cols = st.columns([3, 1])
        cols[0].markdown(
            f"{badge} **{d['origin']}→{d['dest']}** · {d['program']} · {d['cabin'].title()} · "
            f"{d['date']}"
        )
        cpp_s = f"{d['cpp']:.2f}¢" if d.get("cpp") is not None else "N/A"
        cols[1].markdown(f"### {cpp_s}")
        cash_s = f"${d['cash_price']:,.0f}" if d.get("cash_price") is not None else "no cash price"
        flight_s = f" · Flight {d['flight_number']}" if d.get("flight_number") else ""
        st.caption(
            f"{d['points']:,} pts + ${d['taxes']:.2f} {d.get('currency', 'USD')} taxes · "
            f"{cash_s} · checked {d.get('checked_at', '?')}{flight_s}"
        )
