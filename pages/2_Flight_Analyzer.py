import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

import streamlit as st

import award_charts
import flight_search
import going_parse
import ledger
import seats_aero
from cards_data import (
    POOLS,
    all_partner_names,
    find_partner_pools,
    pool_is_active,
    rank_funding_pools,
    transfer_ratio_multiplier,
)
from simulation import run_valuation_simulation

st.set_page_config(page_title="Flight Analyzer — PointsOptimizer", page_icon="✈️", layout="centered")


def _fmt_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _split_datetime(dt_str: str) -> tuple[str, str]:
    """'2026-08-15 08:00' -> ('Aug 15', '08:00')"""
    try:
        date_part, time_part = dt_str.split(" ")
        return datetime.strptime(date_part, "%Y-%m-%d").strftime("%b %d"), time_part
    except (ValueError, AttributeError):
        return dt_str, ""

st.title("Flight Value Analyzer")
st.caption(
    "Paste in a flight you're looking at — the cash price and the points/miles it costs — "
    "and this tells you the value per point, which of your cards can actually get you there, "
    "and whether to book it now or hold your points."
)

st.divider()

# ── Flight inputs ────────────────────────────────────────────────────────────
st.header("The Flight")

st.session_state.setdefault("flight_label_input", "")

with st.popover("📋 Paste a Going deal"):
    st.caption("Paste the text of a Going alert email and I'll pull out what I can.")
    going_text = st.text_area("Deal text", height=150, label_visibility="collapsed")
    if st.button("Parse deal") and going_text:
        deal = going_parse.parse_deal(going_text)
        if not deal.found_anything:
            st.warning("Couldn't find a price or route in that text.")
        else:
            if deal.route_text:
                st.session_state["flight_label_input"] = deal.route_text
            if deal.origin_iata:
                st.session_state["search_origin"] = deal.origin_iata
            if deal.destination_iata:
                st.session_state["search_dest"] = deal.destination_iata
            if deal.price_usd:
                # Going quotes roundtrip totals; award CPP math is usually one-way.
                st.session_state["cash_price_input"] = (
                    deal.price_usd / 2 if deal.is_roundtrip else deal.price_usd
                )
            parsed_bits = [
                f"${deal.price_usd:,.0f}" + (" roundtrip → halved to one-way" if deal.is_roundtrip else "")
                if deal.price_usd else None,
                deal.route_text,
                f"{deal.origin_iata}–{deal.destination_iata}" if deal.origin_iata else None,
                f"travel {deal.raw_dates[0]}" if deal.raw_dates else None,
            ]
            st.success("Parsed: " + " · ".join(b for b in parsed_bits if b))
            st.caption(
                "Note: a cheap cash fare is usually a PAY-CASH signal — run the simulation "
                "and expect it to say hoard your points."
            )

flight_label = st.text_input(
    "Route / description (optional)",
    placeholder="e.g. SFO–NRT business, March",
    key="flight_label_input",
)

partner_options = ["Custom / not listed"] + all_partner_names()
program_choice = st.selectbox("Loyalty program the award is booked through", partner_options)
program_query = (
    st.text_input("Program name", placeholder="e.g. United MileagePlus")
    if program_choice == "Custom / not listed"
    else program_choice
)

st.session_state.setdefault("cash_price_input", 1500.0)

st.subheader("🔍 Search live prices")

if not flight_search.is_configured():
    st.info(
        "Live search isn't configured. Get a free API key at https://serpapi.com (self-serve, "
        "250 searches/month free), then set `SERPAPI_KEY` as an environment variable or in "
        "`.streamlit/secrets.toml` and restart the app. In the meantime, enter the cash price "
        "manually below."
    )
else:
    lc1, lc2, lc3, lc4 = st.columns(4)
    with lc1:
        origin = st.text_input("Origin", placeholder="SFO", max_chars=3, key="search_origin")
    with lc2:
        destination = st.text_input("Destination", placeholder="NRT", max_chars=3, key="search_dest")
    with lc3:
        dep_date = st.date_input("Departure date")
    with lc4:
        cabin = st.selectbox("Cabin", ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"])

    if st.button("Search live prices", type="primary"):
        if not origin or not destination:
            st.error("Enter both airport codes.")
        else:
            st.session_state["flight_offers"] = []
            try:
                with st.spinner("Searching..."):
                    st.session_state["flight_offers"] = flight_search.search_cash_price(
                        origin, destination, dep_date.isoformat(), cabin
                    )
                if not st.session_state["flight_offers"]:
                    st.warning("No offers found for that route/date/cabin.")
            except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
                st.error(str(e))

    for i, offer in enumerate(st.session_state.get("flight_offers", [])[:5]):
        with st.container(border=True):
            head_l, head_r = st.columns([3, 1])
            flight_numbers = ", ".join(s.flight_number for s in offer.segments if s.flight_number)
            head_l.markdown(f"**{offer.airline}** · {flight_numbers} · {offer.cabin.replace('_', ' ').title()}")
            head_r.markdown(f"### ${offer.price_usd:,.0f}")

            dep_date_s, dep_time_s = _split_datetime(offer.departure_time)
            arr_date_s, arr_time_s = _split_datetime(offer.arrival_time)
            stop_label = "Nonstop" if offer.stops == 0 else f"{offer.stops} stop" + ("s" if offer.stops > 1 else "")

            route_l, route_m, route_r = st.columns([2, 3, 2])
            with route_l:
                st.markdown(f"**{dep_time_s}**")
                st.caption(f"{offer.origin} · {dep_date_s}")
            with route_m:
                st.markdown(f"**{_fmt_duration(offer.total_duration_minutes)}**")
                st.caption(stop_label)
            with route_r:
                st.markdown(f"**{arr_time_s}**")
                st.caption(f"{offer.destination} · {arr_date_s}")

            if offer.layovers:
                layover_desc = "; ".join(
                    f"{lay.name or lay.airport} ({lay.airport}) — {_fmt_duration(lay.duration_minutes)} layover"
                    for lay in offer.layovers
                )
                st.caption(f"Via {layover_desc}")

            if len(offer.segments) > 1:
                with st.popover("Flight details"):
                    for seg in offer.segments:
                        seg_dep_date, seg_dep_time = _split_datetime(seg.dep_time)
                        seg_arr_date, seg_arr_time = _split_datetime(seg.arr_time)
                        st.write(
                            f"**{seg.airline} {seg.flight_number}** — "
                            f"{seg.dep_airport} {seg_dep_time} ({seg_dep_date}) → "
                            f"{seg.arr_airport} {seg_arr_time} ({seg_arr_date}) · "
                            f"{_fmt_duration(seg.duration_minutes)}"
                        )

            if st.button("Use this price", key=f"use_offer_{i}", use_container_width=True):
                st.session_state["cash_price_input"] = offer.price_usd
                st.rerun()

st.subheader("🎫 Search award availability")

st.session_state.setdefault("points_required_input", 60000)

if not seats_aero.is_configured():
    st.info(
        "Not configured. Subscribe at https://seats.aero for a developer API key, then set "
        "`SEATS_AERO_API_KEY` as an environment variable or in `.streamlit/secrets.toml` and "
        "restart the app. In the meantime, enter the points required manually below."
    )
else:
    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        award_origin = st.text_input("Origin", placeholder="SFO", max_chars=3, key="award_origin")
    with ac2:
        award_dest = st.text_input("Destination", placeholder="NRT", max_chars=3, key="award_dest")
    with ac3:
        award_date = st.date_input("Departure date", key="award_date")
    with ac4:
        award_cabin = st.selectbox(
            "Cabin", ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"], index=2, key="award_cabin"
        )
    flex_days = st.slider(
        "Flexible +/- days", 0, 7, 0,
        help="Widen the search window around the departure date to catch nearby saver space.",
    )

    if st.button("Search award availability", type="primary"):
        if not award_origin or not award_dest:
            st.error("Enter both airport codes.")
        else:
            st.session_state["award_offers"] = []
            try:
                from datetime import timedelta
                start = award_date - timedelta(days=flex_days)
                end = award_date + timedelta(days=flex_days)
                with st.spinner("Searching seats.aero..."):
                    st.session_state["award_offers"] = seats_aero.search_award_availability(
                        award_origin, award_dest, start.isoformat(), end.isoformat(), award_cabin
                    )
                if not st.session_state["award_offers"]:
                    st.warning("No award space found for that route/date(s)/cabin.")
            except (seats_aero.NotConfigured, seats_aero.SearchFailed) as e:
                st.error(str(e))

    for i, award in enumerate(st.session_state.get("award_offers", [])[:10]):
        with st.container(border=True):
            aw_l, aw_r = st.columns([3, 1])
            partner_badge = "" if award.known_partner else " · not in your wallet's pools"
            aw_l.markdown(
                f"**{award.program}**{partner_badge} · {award.cabin.replace('_', ' ').title()} · "
                f"{award.origin}→{award.destination} · {award.date}"
            )
            aw_r.markdown(f"### {award.points:,} pts")
            stop_label = "Nonstop" if award.direct else "Connection(s)"
            aw_caption = (
                f"+${award.taxes_fees:,.0f} {award.taxes_currency} taxes/fees · {stop_label} · "
                f"{award.remaining_seats} seat(s) left"
            )
            if award.airlines:
                aw_caption += f" · {award.airlines}"
            st.caption(aw_caption)

            if st.button("Use this award", key=f"use_award_{i}", use_container_width=True):
                st.session_state["points_required_input"] = award.points
                st.session_state["taxes_fees_input"] = award.taxes_fees
                if award.known_partner:
                    st.session_state["flight_label_input"] = st.session_state.get(
                        "flight_label_input", ""
                    ) or f"{award.origin}–{award.destination} {award.cabin.title()}"
                st.rerun()

st.divider()
st.subheader("✏️ Or enter manually")

# Sweet-spot reference for the chosen program, with one-click pre-fill
if program_query:
    spots = award_charts.spots_for_program(program_query)
    if spots:
        with st.popover(f"⭐ {len(spots)} known sweet spot(s) for {program_query}"):
            st.caption(
                "Curated reference points, each verified on the date shown. Award pricing "
                "churns — re-verify before booking."
            )
            for j, spot in enumerate(spots):
                sp_l, sp_r = st.columns([4, 1])
                sp_l.markdown(
                    f"**{spot.route}** · {spot.cabin} — **{spot.points_one_way:,} pts** one-way "
                    f"({spot.pricing_model}, verified {spot.last_verified})"
                )
                if spot.notes:
                    sp_l.caption(spot.notes)
                if sp_r.button("Use", key=f"use_spot_{j}"):
                    st.session_state["points_required_input"] = spot.points_one_way
                    st.rerun()

col1, col2, col3 = st.columns(3)
with col1:
    cash_price = st.number_input(
        "Cash price of this flight ($)", min_value=0.0, step=50.0, key="cash_price_input"
    )
with col2:
    points_required = st.number_input(
        "Points/miles required", min_value=1000, step=1000, key="points_required_input"
    )
with col3:
    st.session_state.setdefault("taxes_fees_input", 50.0)
    taxes_fees = st.number_input(
        "Taxes & fees on the award ($)", min_value=0.0, step=5.0, key="taxes_fees_input"
    )

net_value = max(cash_price - taxes_fees, 0.0)
current_cpp = (net_value / points_required) * 100
st.metric("Value per point (CPP)", f"{current_cpp:.2f}¢", help="(cash price − award taxes/fees) / points × 100")

st.divider()

# ── Which of your cards can fund this ───────────────────────────────────────
st.header("Funding This Redemption")

balances = ledger.load_balances()

matches = find_partner_pools(program_query) if program_query else []
if not program_query:
    st.info("Enter a loyalty program above to see which of your cards can reach it.")
elif not matches:
    st.warning(
        f"No transfer partner matched \"{program_query}\" in your wallet's pools. "
        "Check spelling, or this program isn't a partner of any card you hold/plan to get."
    )
else:
    ranked = rank_funding_pools(matches, points_required, balances)

    if len(ranked) > 1:
        best = ranked[0]
        others = ", ".join(r["pool"].currency_name for r in ranked[1:])
        reason = (
            f"it's your least flexible option ({best['flexibility']} partners only it can reach), "
            f"so burning it preserves optionality in {others}"
            if best["flexibility"] <= min(r["flexibility"] for r in ranked[1:])
            else "it best covers the transfer from your saved balances"
        )
        st.info(f"💡 **Burn {best['pool'].currency_name}** — {reason}.")

    for rank_pos, r in enumerate(ranked):
        pool, partner = r["pool"], r["partner"]
        badge = " · ⭐ Recommended" if rank_pos == 0 and len(ranked) > 1 else ""
        with st.container(border=True):
            st.markdown(f"🟢 **{pool.currency_name}** → {partner.name} ({partner.ratio}){badge}")
            st.write(f"Needs **{r['pts_needed']:,.0f}** {pool.currency_name} to get {points_required:,.0f} miles.")
            if r["balance"]:
                if r["covered"]:
                    st.caption(f"✅ Covered — you have {r['balance']:,} saved on the Wallet page.")
                else:
                    st.caption(
                        f"⚠️ Short by {r['pts_needed'] - r['balance']:,.0f} — you have {r['balance']:,} "
                        "saved on the Wallet page."
                    )
            if r["unique_partners"]:
                st.caption(
                    f"Only this pool reaches: {', '.join(r['unique_partners'][:6])}"
                    + ("…" if len(r["unique_partners"]) > 6 else "")
                )

    locked = [(pool, partner) for pool, partner in matches if not pool_is_active(pool.key)]
    for pool, partner in locked:
        pts_needed_in_pool = points_required / transfer_ratio_multiplier(partner.ratio)
        with st.container(border=True):
            st.markdown(f"🟡 **{pool.currency_name}** → {partner.name} ({partner.ratio})")
            st.write(f"Needs **{pts_needed_in_pool:,.0f}** {pool.currency_name} to get {points_required:,.0f} miles.")
            st.caption("Locked — you don't currently hold a card that unlocks transfer for this pool.")

st.divider()

# ── Redeem vs Hoard, reusing the existing simulation engine ────────────────
st.header("Redeem Now or Hoard?")
st.caption("Evaluates this specific deal's CPP against the simulated future value of holding points.")

active_matched_pools = [pool for pool, _ in matches if pool_is_active(pool.key)]
pool_options = {pool.currency_name: pool.key for pool in active_matched_pools}
pool_options["Other / manual"] = None

pool_col, bal_col, rep_col = st.columns(3)
with pool_col:
    chosen_pool_name = st.selectbox("Pool to burn", list(pool_options.keys()))
    chosen_pool_key = pool_options[chosen_pool_name]
with bal_col:
    default_balance = balances.get(chosen_pool_key, 100000) if chosen_pool_key else 100000
    point_balance = st.number_input(
        "Balance in this pool",
        min_value=1000,
        value=max(default_balance, 1000),
        step=1000,
        key=f"sim_balance_{chosen_pool_key}",
        help="Defaults from your saved Wallet balances when a pool is selected.",
    )
with rep_col:
    representative_trip_price = st.number_input(
        "Typical cash value of your future high-value trips ($)",
        min_value=100.0,
        value=1500.0,
        step=100.0,
        help="What a typical trip you'd redeem points for costs in cash — NOT this flight's "
        "price. The simulation values hoarded points against future trips like this one.",
    )

with st.expander("Simulation settings", expanded=False):
    c3, c4 = st.columns(2)
    with c3:
        lambda_trips = st.slider("Expected high-value trips per year (λ)", 0.5, 10.0, 2.0, 0.5)
        time_horizon = st.selectbox("Time horizon (years)", [3, 5], index=0)
        depreciation_pct = st.slider("Annual point devaluation rate (%)", 1, 20, 5)
    with c4:
        mu_cost = st.slider("Avg log-cost of future trips (μ)", 9.0, 13.0, 11.0, 0.5)
        sigma_cost = st.slider("Variability of future trip costs (σ)", 0.1, 1.5, 0.5, 0.1)
        market_return_pct = st.slider("Annual opportunity cost of cash (%)", 1, 15, 5)

depreciation_rate = depreciation_pct / 100
market_return = market_return_pct / 100

if st.button("Run Simulation", type="primary", use_container_width=True):
    with st.spinner("Running 10,000 iterations..."):
        result = run_valuation_simulation(
            current_cpp=current_cpp,
            point_balance=point_balance,
            cash_price=representative_trip_price,
            time_horizon=time_horizon,
            lambda_trips=lambda_trips,
            mu_cost=mu_cost,
            sigma_cost=sigma_cost,
            depreciation_rate=depreciation_rate,
            market_return=market_return,
        )

    ledger.append_history(
        route=flight_label or "",
        program=program_query or "",
        pool_key=chosen_pool_key or "",
        cash_price=cash_price,
        taxes_fees=taxes_fees,
        points_required=points_required,
        cpp=result.current_cpp,
        avg_simulated_cpp=result.avg_simulated_cpp,
        verdict="redeem" if result.recommend_redeem else "hoard",
    )

    label = flight_label or "This flight"
    if result.recommend_redeem:
        st.success(f"## Recommendation: BOOK IT")
        st.write(
            f"{label} at **{result.current_cpp:.2f}¢/pt** beats the simulated future average of "
            f"**{result.avg_simulated_cpp:.2f}¢/pt**. Lock it in."
        )
    else:
        st.warning(f"## Recommendation: HOLD")
        st.write(
            f"{label} at **{result.current_cpp:.2f}¢/pt** is below the simulated future average of "
            f"**{result.avg_simulated_cpp:.2f}¢/pt**. A better redemption is likely to come along."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("This deal's CPP", f"{result.current_cpp:.2f}¢")
    c2.metric("Avg Simulated CPP", f"{result.avg_simulated_cpp:.2f}¢")
    c3.metric("5th Percentile", f"{result.percentile_5:.2f}¢")
    c4.metric("95th Percentile", f"{result.percentile_95:.2f}¢")
    st.caption(
        f"Based on {result.iterations:,} Monte Carlo iterations · "
        f"Avg {result.avg_trips_per_year:.1f} trips/year drawn · {time_horizon}-year horizon"
    )
