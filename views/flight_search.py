import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta

import streamlit as st

import airports
import cash_quotes
import digest_view
import flight_search
import funding
import places
import route_search
import seats_aero
import valuation
from cards_data import all_partner_names

CABINS = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]
BADGE = {"BOOK": "🟢 Book", "BORDERLINE": "🟡 Borderline", "SKIP": "🔴 Skip"}


def _cabin(c: str) -> str:
    return c.replace("_", " ").title()


def _pay_block(program: str, points: int, key: str) -> None:
    p = funding.plan(program, points)
    st.markdown(f"💳 **{places.md_safe(p.summary)}**")


def _date_range(label: str, value: tuple[date, date], key: str) -> tuple[date | None, date | None]:
    v = st.date_input(label, value=value, min_value=date.today(),
                      max_value=date.today() + timedelta(days=cash_quotes.MAX_LOOKAHEAD_DAYS), key=key)
    return (v[0], v[1]) if isinstance(v, (tuple, list)) and len(v) == 2 else (None, None)


st.title("✈️ Flight Search")
st.caption(
    "Find award seats on a route and see what each is worth in cents per point against the live "
    "Google Flights fare, plus exactly which of your points or miles to pay with."
)

if not seats_aero.is_configured():
    st.warning("Award search needs SEATS_AERO_API_KEY in secrets.")
    st.stop()

# ── 1. Search ────────────────────────────────────────────────────────────────
opts = airports.options()
mode = st.radio("When", ["One date", "Date range", "Any time", "Outbound + return"], horizontal=True,
                key="fs_mode",
                help="Outbound + return searches each direction as a separate one-way award, which is "
                     "usually better value on points than a round-trip award.")
c1, c2, c3 = st.columns(3)
with c1:
    origin = st.selectbox("From", opts, index=opts.index(airports.DEFAULT_ORIGIN_OPTION), key="fs_origin",
                          accept_new_options=True, help="Pick a city, or type any 3-letter airport code")
with c2:
    dest = st.selectbox("To", opts, index=None, placeholder="Type a city or airport code…", key="fs_dest",
                        accept_new_options=True)
with c3:
    cabins = st.multiselect("Cabins", CABINS, default=["ECONOMY", "BUSINESS"], format_func=_cabin, key="fs_cabins")

today = date.today()
if mode == "One date":
    d1, d2 = st.columns([1, 2])
    with d1:
        one_day = st.date_input("Date", value=today + timedelta(days=30), min_value=today, key="fs_date")
    with d2:
        flex = st.slider("Flexible ± days", 0, 7, 2, key="fs_flex")
elif mode == "Date range":
    rng = _date_range("Travel between", (today + timedelta(days=30), today + timedelta(days=90)), "fs_range")
elif mode == "Outbound + return":
    r1, r2 = st.columns(2)
    with r1:
        out_rng = _date_range("Outbound between", (today + timedelta(days=60), today + timedelta(days=75)), "fs_out")
    with r2:
        ret_rng = _date_range("Return between", (today + timedelta(days=67), today + timedelta(days=85)), "fs_ret")
else:
    st.caption("Searches every date from tomorrow to about 11 months out.")

if st.button("Find the best deals", type="primary"):
    o_code, _ = airports.resolve_input(origin)
    d_code, _ = airports.resolve_input(dest)
    error = None
    if not origin or not dest:
        error = "Pick both a From and a To."
    elif not o_code or not d_code:
        error = f"Couldn't recognize \"{origin if not o_code else dest}\"; try the 3-letter airport code (e.g. JFK)."
    elif o_code == d_code:
        error = "From and To are the same place."
    elif not cabins:
        error = "Pick at least one cabin."
    if not error and mode == "Outbound + return" and not all(out_rng + ret_rng):
        error = "Pick a start and end date for both outbound and return."
    if not error and mode == "Date range" and not all(rng):
        error = "Pick a start and end date."
    if error:
        st.error(error)
    else:
        try:
            with st.status("Searching…", expanded=True) as status:
                if mode == "Outbound + return":
                    result = ("pair", route_search.search_pair(o_code, d_code, cabins, *out_rng, *ret_rng,
                                                               log=status.write))
                else:
                    if mode == "One date":
                        lo, hi = one_day - timedelta(days=flex), one_day + timedelta(days=flex)
                    elif mode == "Date range":
                        lo, hi = rng
                    else:
                        lo, hi = None, None
                    result = ("single", route_search.search(o_code, d_code, cabins, lo, hi, log=status.write))
                status.update(label="Done", state="complete", expanded=False)
            when = (f"{places.nice_date(lo.isoformat(), weekday=False)} – "
                    f"{places.nice_date(hi.isoformat(), weekday=False)}" if mode != "Outbound + return" and lo
                    else ("any date" if mode == "Any time" else "out and back"))
            st.session_state["fs_result"] = result
            st.session_state["fs_result_label"] = (
                f"{places.airport_label(o_code)} → {places.airport_label(d_code)} · {when} · "
                + ", ".join(_cabin(c) for c in cabins))
        except (ValueError, seats_aero.NotConfigured, seats_aero.SearchFailed) as e:
            st.error(str(e))

# ── 2. Results ───────────────────────────────────────────────────────────────
result = st.session_state.get("fs_result")
if result:
    kind, r = result
    st.divider()
    st.subheader(st.session_state.get("fs_result_label", "Results"))
    st.caption("Best value per point first, against live Google Flights fares.")
    if kind == "pair":
        if r.best_pair:
            o, b = r.best_pair
            st.success(
                f"**Best out-and-back: {r.combined_cpp:.2f}¢ per point** · out "
                f"{places.nice_date(o.c.date)} on {o.c.program} ({o.c.points:,}) and back "
                f"{places.nice_date(b.c.date)} or later on {b.c.program} ({b.c.points:,}): "
                f"{o.c.points + b.c.points:,} points total"
            )
            if r.pair_round_trip_fare and r.pair_round_trip_fare < r.pair_cash_one_ways:
                st.caption(places.md_safe(f"Valued against the ${r.pair_round_trip_fare:,.0f} round-trip fare, lower "
                                          f"than the two one-way fares (${r.pair_cash_one_ways:,.0f})."))
        elif r.outbound.deals and r.inbound.deals:
            st.warning("No return date falls after an outbound date in these ranges.")
        sections = [("Outbound", r.outbound), ("Return", r.inbound)]
    else:
        sections = [("", r)]
    for title, res in sections:
        if title:
            st.markdown(f"**{title}**")
        for n in res.notes:
            st.info(n)
        st.caption(f"{res.awards_found:,} award seats you can pay for · {res.priced} priced · best value first")
        if not res.deals:
            st.write("No priced deals for this search.")
        for s in res.deals[:12]:
            digest_view.deal_card(s.to_dict())

# ── Tools ────────────────────────────────────────────────────────────────────
st.divider()
with st.expander("🧮 Check a deal by hand"):
    h1, h2, h3 = st.columns(3)
    cash = h1.number_input("Cash fare ($)", min_value=0.0, value=0.0, step=50.0)
    pts = h2.number_input("Points / miles", min_value=0, value=0, step=1000)
    taxes = h3.number_input("Award taxes & fees ($)", min_value=0.0, value=0.0, step=5.0)
    h4, h5 = st.columns(2)
    hand_cabin = h4.selectbox("Cabin", CABINS, format_func=_cabin, key="hand_cabin")
    program = h5.selectbox("Program", ["—"] + all_partner_names(), key="hand_program")
    if cash and pts:
        cpp = valuation.compute_cpp(cash, taxes, int(pts))
        verdict = valuation.verdict_for(cpp, hand_cabin)
        st.metric("Cents per point", f"{cpp:.2f}¢")
        st.write(f"{BADGE.get(verdict, verdict)} · the bar for {_cabin(hand_cabin)} is "
                 f"{valuation.great_floor(hand_cabin):.1f}¢")
        if taxes > cash:
            st.warning("The award's taxes cost more than the cash fare: pay cash.")
        if program != "—":
            _pay_block(program, int(pts), key="hand")

with st.expander("💵 Look up cash fares"):
    f1, f2, f3, f4 = st.columns(4)
    f_origin = f1.selectbox("From", opts, index=opts.index(airports.DEFAULT_ORIGIN_OPTION), key="cf_origin",
                            accept_new_options=True)
    f_dest = f2.selectbox("To", opts, index=None, placeholder="City or code…", key="cf_dest", accept_new_options=True)
    f_date = f3.date_input("Date", value=today + timedelta(days=30), min_value=today, key="cf_date")
    f_cabin = f4.selectbox("Cabin", CABINS, format_func=_cabin, key="cf_cabin")
    if st.button("Search cash fares"):
        oc, _ = airports.resolve_input(f_origin)
        dc, _ = airports.resolve_input(f_dest)
        if not (oc and dc) or oc == dc:
            st.error("Pick a valid From and To.")
        else:
            try:
                with st.spinner("Searching Google Flights…"):
                    st.session_state["cf_offers"] = flight_search.search_cash_price(oc, dc, f_date.isoformat(), f_cabin)
            except (flight_search.NotConfigured, flight_search.SearchFailed) as e:
                st.error(str(e))
    for offer in st.session_state.get("cf_offers", [])[:8]:
        dep = datetime.strptime(offer.departure_time, "%Y-%m-%d %H:%M") if offer.departure_time else None
        arr = datetime.strptime(offer.arrival_time, "%Y-%m-%d %H:%M") if offer.arrival_time else None
        stops = "Nonstop" if offer.stops == 0 else f"{offer.stops} stop{'s' if offer.stops > 1 else ''}"
        via = f" via {', '.join(places.city(l.airport) for l in offer.layovers)}" if offer.layovers else ""
        hours, mins = divmod(offer.total_duration_minutes, 60)
        st.write(f"**\\${offer.price_usd:,.0f}** · {places.airline_names(','.join(offer.carrier_codes)) or offer.airline} · "
                 f"{dep:%a %b %-d %H:%M} → {arr:%H:%M} · {hours}h {mins}m · {stops}{via}"
                 if dep and arr else f"**\\${offer.price_usd:,.0f}** · {offer.airline} · {stops}{via}")
