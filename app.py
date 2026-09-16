import os
import sys

# This is the app entrypoint (repo root); keep root importable regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, datetime

import streamlit as st

import airports
import award_charts
import cash_quotes
import check_alerts
import deal_log
import flight_search
import going_parse
import ledger
import return_finder
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

st.title("✈️ Flight Value Analyzer")
st.caption(
    "Paste in a flight you're looking at — the cash price and the points/miles it costs — "
    "and this tells you the value per point, which of your cards can actually get you there, "
    "and whether to book it now or hold your points."
)

# ── At-a-glance summary (always visible, reflects the current inputs below) ───
st.session_state.setdefault("cash_price_input", 1500.0)
st.session_state.setdefault("points_required_input", 60000)
st.session_state.setdefault("taxes_fees_input", 50.0)

st.session_state.setdefault("value_cabin", "ECONOMY")

# One verdict rule for the whole page (and Deal Radar): deal_log.verdict_for, cabin-aware.
_VERDICT_LABEL = {"BOOK": "✅ Book", "BORDERLINE": "🟡 Borderline", "SKIP": "⛔ Skip"}


def _mark_value_touched() -> None:
    st.session_state["_value_touched"] = True


sm1, sm2, sm3, sm4 = st.columns(4)
if st.session_state.get("_value_touched"):
    _cp = st.session_state["cash_price_input"]
    _pts = st.session_state["points_required_input"]
    _tx = st.session_state["taxes_fees_input"]
    _cpp_now = check_alerts.compute_cpp(_cp, _tx, int(_pts))
    sm1.metric("Cash price", f"${_cp:,.0f}")
    sm2.metric("Points", f"{_pts:,}")
    sm3.metric("Value per point", f"{_cpp_now:.2f}¢" if _cpp_now is not None else "—")
    sm4.metric("Quick verdict", _VERDICT_LABEL.get(deal_log.verdict_for(_cpp_now, st.session_state["value_cabin"]), "—"))
else:
    sm1.metric("Cash price", "—")
    sm2.metric("Points", "—")
    sm3.metric("Value per point", "—")
    sm4.metric("Quick verdict", "—")
    st.caption("Search an award or enter a cash price and points in ② to get a verdict.")

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
            # search_origin/search_dest accept free text now, so fall back to the
            # raw code for airports not in the curated dropdown list.
            if deal.origin_iata:
                st.session_state["search_origin"] = airports.option_from_code(deal.origin_iata) or deal.origin_iata.upper()
            if deal.destination_iata:
                st.session_state["search_dest"] = airports.option_from_code(deal.destination_iata) or deal.destination_iata.upper()
            if deal.price_usd:
                st.session_state["_value_touched"] = True
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

# Apply a deferred label prefill from a prior "Use this award" click. Must happen
# BEFORE the widget is instantiated -- writing a widget-keyed session_state value
# after its widget exists raises StreamlitAPIException.
_label_prefill = st.session_state.pop("_label_prefill", None)
if _label_prefill and not st.session_state.get("flight_label_input"):
    st.session_state["flight_label_input"] = _label_prefill
_program_prefill = st.session_state.pop("_program_prefill", None)

flight_label = st.text_input(
    "Route / description (optional)",
    placeholder="e.g. SFO–NRT business, March",
    key="flight_label_input",
)

partner_options = ["Custom / not listed"] + all_partner_names()
if _program_prefill in partner_options:
    st.session_state["program_choice"] = _program_prefill
program_choice = st.selectbox("Loyalty program the award is booked through", partner_options, key="program_choice")
program_query = (
    st.text_input("Program name", placeholder="e.g. United MileagePlus")
    if program_choice == "Custom / not listed"
    else program_choice
)

st.header("① Search this flight")
st.caption("Plug in a flight from a seats.aero alert (or anywhere): find the award, then value it.")
tab_award, tab_price = st.tabs(["🎫 Award availability", "🔍 Cash price"])

with tab_price:
    if not flight_search.is_configured():
        st.info(
            "Live cash search isn't available: install `fast-flights` (free, see requirements.txt) "
            "or set `SERPAPI_KEY`. In the meantime, enter the cash price manually below."
        )
    else:
        _AIRPORTS = airports.options()
        lc1, lc2, lc3, lc4 = st.columns(4)
        with lc1:
            origin = st.selectbox("Origin", _AIRPORTS, index=_AIRPORTS.index(airports.DEFAULT_ORIGIN_OPTION),
                                  key="search_origin", accept_new_options=True,
                                  help="Pick from the list, or type any city or 3-letter airport code")
        with lc2:
            destination = st.selectbox("Destination", _AIRPORTS, index=None,
                                       placeholder="Type a city or airport code…", key="search_dest",
                                       accept_new_options=True)
        with lc3:
            dep_date = st.date_input("Departure date", min_value=date.today())
        with lc4:
            cabin = st.selectbox("Cabin", ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"])

        if st.button("Search live prices", type="primary"):
            o_code, _ = airports.resolve_input(origin)
            d_code, _ = airports.resolve_input(destination)
            if not origin or not destination:
                st.error("Pick both an origin and a destination.")
            elif not o_code or not d_code:
                bad = origin if not o_code else destination
                st.error(f"Couldn't recognize \"{bad}\" — try the 3-letter airport code instead (e.g. JFK).")
            elif o_code == d_code:
                st.error("Origin and destination are the same airport.")
            else:
                st.session_state["flight_offers"] = []
                try:
                    with st.spinner("Searching..."):
                        st.session_state["flight_offers"] = flight_search.search_cash_price(
                            o_code, d_code, dep_date.isoformat(), cabin
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
                    st.session_state["value_cabin"] = offer.cabin
                    st.session_state["_value_touched"] = True
                    st.rerun()

with tab_award:
    if not seats_aero.is_configured():
        st.info(
            "Not configured. Subscribe at https://seats.aero for a developer API key, then set "
            "`SEATS_AERO_API_KEY` as an environment variable or in `.streamlit/secrets.toml` and "
            "restart the app. In the meantime, enter the points required manually below."
        )
    else:
        _AWPORTS = airports.options()
        ac1, ac2, ac3, ac4 = st.columns(4)
        with ac1:
            award_origin = st.selectbox("Origin", _AWPORTS, index=_AWPORTS.index(airports.DEFAULT_ORIGIN_OPTION),
                                        key="award_origin", accept_new_options=True,
                                        help="Pick from the list, or type any city or 3-letter airport code")
        with ac2:
            award_dest = st.selectbox("Destination", _AWPORTS, index=None,
                                      placeholder="Type a city or airport code…", key="award_dest",
                                      accept_new_options=True)
        with ac3:
            award_date = st.date_input("Departure date", key="award_date", min_value=date.today())
        with ac4:
            award_cabin = st.selectbox(
                "Cabin", ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"], index=2, key="award_cabin"
            )
        flex_days = st.slider(
            "Flexible +/- days", 0, 7, 0,
            help="Widen the search window around the departure date to catch nearby saver space.",
        )

        if st.button("Search award availability", type="primary"):
            o_code, _ = airports.resolve_input(award_origin)
            d_code, _ = airports.resolve_input(award_dest)
            if not award_origin or not award_dest:
                st.error("Pick both an origin and a destination.")
            elif not o_code or not d_code:
                bad = award_origin if not o_code else award_dest
                st.error(f"Couldn't recognize \"{bad}\" — try the 3-letter airport code instead (e.g. JFK).")
            elif o_code == d_code:
                st.error("Origin and destination are the same airport.")
            else:
                st.session_state["award_offers"] = []
                try:
                    from datetime import timedelta
                    start = award_date - timedelta(days=flex_days)
                    end = award_date + timedelta(days=flex_days)
                    with st.spinner("Searching seats.aero..."):
                        st.session_state["award_offers"] = seats_aero.search_award_availability(
                            o_code, d_code, start.isoformat(), end.isoformat(), award_cabin
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

                akey = "|".join([str(award.source), award.origin, award.destination,
                                 str(award.date), str(award.points), award.cabin])
                if st.button("Use this award — get CPP", key=f"use_award_{i}", use_container_width=True):
                    taxes_usd = award.taxes_fees * check_alerts._fx_rate(award.taxes_currency)
                    st.session_state["points_required_input"] = award.points
                    st.session_state["taxes_fees_input"] = round(taxes_usd, 2)
                    if award.known_partner:
                        # Can't write flight_label_input here -- its widget is above, already
                        # instantiated this run. Defer via a flag applied before the widget next run.
                        st.session_state["_label_prefill"] = (
                            f"{award.origin}–{award.destination} {award.cabin.title()}"
                        )
                    st.session_state["value_cabin"] = award.cabin
                    st.session_state["_value_touched"] = True
                    if award.known_partner:
                        st.session_state["_program_prefill"] = award.program
                    result = {"taxes_usd": taxes_usd, "cash": None, "cpp": None, "verdict": None,
                              "note": None, "approx": False}
                    try:
                        with st.spinner("Looking up the cash fare for this route/date/cabin..."):
                            quote = cash_quotes.get_quote(award.origin, award.destination, award.date, award.cabin)
                        comp = (cash_quotes.comparable_fare(quote, award.direct, award.airlines)
                                if quote is not None else None)
                        if comp is not None:
                            st.session_state["cash_price_input"] = comp.price
                            cpp = check_alerts.compute_cpp(comp.price, taxes_usd, award.points)
                            result.update(cash=comp.price, cpp=cpp, approx=quote.approx, basis=comp.basis,
                                          same_carrier=comp.same_carrier_price,
                                          verdict=deal_log.verdict_for(cpp, award.cabin))
                        else:
                            result["note"] = ("No cash fare found for this route/date — "
                                              "enter it in ② below to get CPP.")
                    except cash_quotes.OutOfWindow as e:
                        result["note"] = f"Can't price cash fares for this date ({e}). Enter it in ② below."
                    except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
                        result["note"] = f"Cash-price lookup failed ({e}). Enter it in ② below."
                    st.session_state[f"award_result_{akey}"] = result
                    st.rerun()

                # Inline CPP result populated by "Use this award — get CPP".
                res = st.session_state.get(f"award_result_{akey}")
                if res and res.get("cpp") is not None:
                    badge = {"BOOK": "🟢", "BORDERLINE": "🟡", "SKIP": "🔴"}.get(res["verdict"], "⚪")
                    rc1, rc2 = st.columns([3, 1])
                    rc1.markdown(f"{badge} **{res['verdict']}** — this flight")
                    rc1.caption(
                        f"{award.cabin.replace('_', ' ').lower()} {res.get('basis') or 'cash fare'} ${res['cash']:,.0f}"
                        + (f" (award airline's own fare ${res['same_carrier']:,.0f})" if res.get("same_carrier") else "")
                        + (" (from a date within 7 days)" if res.get("approx") else "")
                        + f" − ${res['taxes_usd']:.0f} taxes over {award.points:,} pts "
                        "· full funding + hoard-vs-redeem below"
                    )
                    rc2.metric("CPP", f"{res['cpp']:.2f}¢")
                elif res and res.get("note"):
                    st.info(res["note"])

                # Interested in this departure? Find a return leg on the reverse route.
                _outbound = {"origin": award.origin, "dest": award.destination, "date": award.date,
                             "cabin": award.cabin, "points": award.points}
                if res and res.get("cpp") is not None:
                    _outbound.update(cpp=res["cpp"], cash_price=res["cash"], taxes_usd=res["taxes_usd"])
                return_finder.render(_outbound, key=f"fa_award_{akey}")

st.divider()
st.header("② Value & verdict")
st.caption("Pulled from your searches above, or enter/adjust manually.")

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

_CABINS = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]
col1, col2, col3, col4 = st.columns(4)
with col1:
    cash_price = st.number_input(
        "Cash price of this flight ($)", min_value=0.0, step=50.0, key="cash_price_input",
        on_change=_mark_value_touched,
    )
with col2:
    points_required = st.number_input(
        "Points/miles required", min_value=1, step=1000, key="points_required_input",
        on_change=_mark_value_touched,
    )
with col3:
    taxes_fees = st.number_input(
        "Taxes & fees on the award ($)", min_value=0.0, step=5.0, key="taxes_fees_input",
        on_change=_mark_value_touched,
    )
with col4:
    value_cabin = st.selectbox("Cabin", _CABINS, key="value_cabin",
                               format_func=lambda c: c.replace("_", " ").title())

current_cpp = check_alerts.compute_cpp(cash_price, taxes_fees, int(points_required))
st.metric("Value per point (CPP)", f"{current_cpp:.2f}¢", help="(cash price − award taxes/fees) / points × 100")
if taxes_fees > cash_price:
    st.warning("Award taxes exceed the cash price — this redemption saves nothing; pay cash.")

_great = deal_log.great_floor(value_cabin)
_verdict = deal_log.verdict_for(current_cpp, value_cabin)
if not st.session_state.get("_value_touched"):
    st.caption("Enter a cash price and points above to get a verdict.")
elif _verdict == "SKIP":
    st.error(f"⛔ **Skip** — {current_cpp:.2f}¢/pt is below {deal_log.SKIP_CPP:.1f}¢.")
elif _verdict == "BOOK":
    st.success(f"✅ **Book it** — {current_cpp:.2f}¢/pt clears the {_great:.1f}¢ bar for {value_cabin.replace('_', ' ').title()}.")
else:
    st.warning(f"🟡 **Borderline** — {current_cpp:.2f}¢/pt (book bar for {value_cabin.replace('_', ' ').title()} is "
               f"{_great:.1f}¢). Run the simulation below for a fuller answer.")

st.divider()

# ── Which of your cards can fund this ───────────────────────────────────────
st.header("③ Funding this redemption")

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
st.header("④ Redeem now or hoard?")
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
