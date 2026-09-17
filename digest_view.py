"""Renders a Deal Finder digest (deal_digest.json or a fresh scan result)."""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

import deal_finder
import funding
import ledger
import places


def load_digest() -> dict | None:
    try:
        with open(deal_finder.DIGEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def render_digest(digest: dict | None = None) -> None:
    digest = digest if digest is not None else load_digest()
    if not digest:
        st.info("No deal scan yet. Click **Scan now** above, or wait for the daily 7am scan.")
        return
    top = digest.get("top", [])
    scan = digest.get("scan", {})
    held = digest.get("held_miles", [])
    if held:
        st.header("✅ Book now with miles you already hold")
        for d in held:
            deal_card(d)
    for group in digest.get("watchlist", []):
        st.header(f"⭐ Watchlist: {group['label']}")
        if not group["deals"]:
            st.caption(f"No deal clears this entry's bar yet ({group['matched_awards']:,} matching awards, "
                       f"{group['priced']} priced).")
        for d in group["deals"]:
            deal_card(d, bar=d.get("watch_bar"), surplus=d.get("watch_surplus_usd"))
    st.header(f"🏆 Top {len(top)} of {scan.get('candidates', 0):,} awards")
    st.caption(
        f"Programs: {', '.join(scan.get('sources', []))}. "
        "Ranked by dollars saved above your cabin's great-deal bar (1.5¢ Economy, 2.0¢ Business/First), "
        "using live Google Flights cash fares. One entry per destination + cabin; other dates, "
        "origins and programs are listed under it. Award space moves fast: re-check on seats.aero."
    )
    for i, d in enumerate(top):
        deal_card(d, rank=i + 1)


@st.cache_data(ttl=60, show_spinner=False)
def _balances() -> tuple[dict, dict]:
    return ledger.load_balances(), ledger.load_program_balances()


def _hm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%-I:%M %p")
    except (ValueError, AttributeError):
        return ""


def _days_later(dep: str, arr: str) -> str:
    try:
        n = (datetime.fromisoformat(arr[:10]) - datetime.fromisoformat(dep[:10])).days
    except (ValueError, TypeError):
        return ""
    return f" (+{n} day{'s' if n > 1 else ''})" if n > 0 else ""


def _duration(minutes: int) -> str:
    h, m = divmod(int(minutes or 0), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def deal_card(d: dict, rank: int | None = None, bar: float | None = None, surplus: float | None = None) -> None:
    """One deal: headline numbers, then flight / how to pay / dates, then links."""
    safe = places.md_safe
    bar = bar if bar is not None else d["great_floor"]
    surplus = surplus if surplus is not None else d["surplus_usd"]
    trip = d.get("trip") or {}
    cabin = d["cabin"].replace("_", " ").title()
    with st.container(border=True):
        title = f"{rank}. " if rank else ""
        title += f"{places.airport_label(d['origin'])} → {places.airport_label(d['dest'])}"
        tags = [cabin, d["program"]]
        if d.get("new"):
            tags.append("🆕 new")
        st.markdown(f"#### {safe(title)}")
        st.caption(" · ".join(safe(t) for t in tags))

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Value", f"{d['cpp']:.2f}¢/pt")
        m2.metric("Points", f"{d['points']:,}", help=f"Plus ${d['taxes_usd']:,.0f} in taxes and fees")
        m3.metric("Cash fare", f"${d['cash_price']:,.0f}", help=d.get("cash_basis") or None)
        m4.metric("Saved vs bar", f"${surplus:,.0f}", help=f"Dollars above the {bar:.1f}¢/pt bar for {cabin}")
        st.caption(safe(f"Plus ${d['taxes_usd']:,.0f} in taxes · cash fare: {d.get('cash_basis') or 'cheapest fare'}"
                        + (" (from a date within 7 days)" if d.get("cash_is_approx") else "")
                        + (" · first class compared with the business fare" if d["cabin"] == "FIRST" else "")))

        left, right = st.columns([3, 2], gap="medium")
        with left:
            st.markdown("**✈️ Flight**")
            date_line = places.nice_date(d["date"])
            if trip:
                stops = len(trip.get("connections") or [])
                via = f" via {', '.join(places.city(c) for c in trip['connections'])}" if stops else ""
                plus = _days_later(trip["departs_at"], trip["arrives_at"])
                date_line += (f" · {_hm(trip['departs_at'])} → {_hm(trip['arrives_at'])}{plus}"
                              f" · {_duration(trip['duration_min'])} · "
                              + ("Nonstop" if not stops else f"{stops} stop{'s' if stops > 1 else ''}{via}"))
                st.write(safe(date_line))
                st.caption(safe(" · ".join(trip.get("flights") or []) + (f" · {trip['carriers']}" if trip.get("carriers") else "")))
                if trip.get("mixed_cabin"):
                    st.warning(safe("Mixed cabin: " + ", ".join(trip["lower_cabin_legs"]) + ". Worth less than the CPP shows."),
                               icon="⚠️")
                if trip.get("airport_changes"):
                    st.warning("Airport change mid-trip: " + ", ".join(trip["airport_changes"])
                               + ". You'd have to get between airports yourself.", icon="🚕")
                if d.get("slow"):
                    st.caption("🐢 Much longer than flying there directly.")
            else:
                st.write(safe(date_line + (" · Nonstop" if d.get("direct") else " · Connecting")))
                if d.get("airlines"):
                    st.caption(safe(places.airline_names(d["airlines"])))
            if d.get("other_dates"):
                st.markdown("**📅 Also available**")
                st.caption(", ".join(places.nice_date(x, weekday=False) for x in d["other_dates"][:12])
                           + (f" and {len(d['other_dates']) - 12} more" if len(d["other_dates"]) > 12 else ""))
            if d.get("alternatives"):
                st.markdown("**🔁 Other ways**")
                st.caption(safe(" · ".join(d["alternatives"])))
        with right:
            st.markdown("**💳 How to pay**")
            balances, programs = _balances()
            plan = funding.plan(d["program"], d["points"], balances, programs)
            st.write(safe(plan.summary))

        links = []
        if trip.get("booking_url"):
            links.append(f"[{safe(trip.get('booking_label') or 'Book')} →]({trip['booking_url']})")
        from urllib.parse import quote
        gf = "https://www.google.com/travel/flights?q=" + quote(f"Flights from {d['origin']} to {d['dest']} on {d['date']} one way")
        links.append(f"[Check the cash fare on Google Flights →]({gf})")
        age = d.get("age_days")
        age_s = "" if age is None else (" · award seen today" if age < 1 else f" · award seen {age:.0f} day{'s' if age >= 1.5 else ''} ago")
        st.caption(" · ".join(links) + age_s + (" (may be gone)" if (age or 0) > 5 else ""))
