import os
import sys

# This is the app entrypoint (repo root); keep root importable regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
import places
import return_finder
import route_search
import seats_aero
from cards_data import (
    all_partner_names,
    find_partner_pools,
    pool_is_active,
    rank_funding_pools,
    transfer_ratio_multiplier,
)


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

def _use_deal(s) -> None:
    """Fill ② with a priced route-search deal (a deal_finder.Scored)."""
    st.session_state["points_required_input"] = s.c.points
    st.session_state["taxes_fees_input"] = round(s.taxes_usd, 2)
    st.session_state["cash_price_input"] = float(s.cash)
    st.session_state["value_cabin"] = s.c.cabin
    st.session_state["_value_touched"] = True
    st.session_state["_label_prefill"] = f"{s.c.origin}–{s.c.dest} {s.c.cabin.title()} {s.c.date}"
    if s.c.program in all_partner_names():
        st.session_state["_program_prefill"] = s.c.program


def _route_deal_row(s, key: str) -> None:
    d = s.to_dict()
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        verdict = deal_log.verdict_for(d["cpp"], d["cabin"])
        badge = {"BOOK": "🟢", "BORDERLINE": "🟡", "SKIP": "🔴"}.get(verdict, "⚪")
        c1.markdown(f"{badge} **{places.airport_label(d['origin'])} → {places.airport_label(d['dest'])}**")
        c1.caption(f"{d['program']} · {d['cabin'].replace('_', ' ').title()} · {places.nice_date(d['date'])} · "
                   f"{d['points']:,} pts + ${d['taxes_usd']:.0f} taxes"
                   f"{' · nonstop' if d['direct'] else ''}"
                   f"{' · ' + places.airline_names(d['airlines']) if d['airlines'] else ''}")
        c1.caption(f"vs ${d['cash_price']:,.0f} ({d['cash_basis']})"
                   + (" · ✅ bookable with miles you hold" if d["bookable_now"] else ""))
        if d["other_dates"]:
            c1.caption(f"Same price on {len(d['other_dates'])} other date(s): {', '.join(d['other_dates'][:10])}"
                       + (" …" if len(d["other_dates"]) > 10 else ""))
        c2.metric("CPP", f"{d['cpp']:.2f}¢")
        if c2.button("Use", key=key):
            _use_deal(s)
            st.rerun()


def _render_route_result(kind: str, result) -> None:
    if kind == "single":
        results = [("", result)]
    else:
        results = [("Outbound", result.outbound), ("Return", result.inbound)]
        if result.best_pair:
            o, r = result.best_pair
            st.success(
                f"**Best pair: {result.combined_cpp:.2f}¢/pt combined** — out {o.c.date} "
                f"({o.c.program}, {o.c.points:,} pts) and back from {r.c.date} "
                f"({r.c.program}, {r.c.points:,} pts): {o.c.points + r.c.points:,} pts total."
            )
            if result.pair_round_trip_fare and result.pair_round_trip_fare < result.pair_cash_one_ways:
                st.caption(f"Valued against the ${result.pair_round_trip_fare:,.0f} round-trip fare, which is "
                           f"lower than the two one-way fares (${result.pair_cash_one_ways:,.0f}).")
        elif result.outbound.deals and result.inbound.deals:
            st.warning("No return date falls after an outbound date in these ranges.")
    for label, r in results:
        if label:
            st.subheader(label)
        for n in r.notes:
            st.info(n)
        st.caption(f"{r.awards_found:,} awards you can fund · {r.priced} priced · best cents per point first")
        if not r.deals:
            st.write("No priced deals.")
        for i, s in enumerate(r.deals[:10]):
            _route_deal_row(s, key=f"route_use_{label}_{i}")


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
                "Note: a cheap cash fare is usually a pay-cash signal: its CPP will likely "
                "fall below the bar."
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
        _CABIN_CHOICES = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]
        award_mode = st.radio(
            "When", ["One date", "Date range", "Any time", "Outbound + return"], horizontal=True,
            key="award_mode",
            help="Date range / Any time: best cents-per-point awards on this route across the dates, "
                 "priced against live Google Flights fares. Outbound + return: two separate one-way "
                 "awards (usually better value on points than a round-trip award).",
        )
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            award_origin = st.selectbox("Origin", _AWPORTS, index=_AWPORTS.index(airports.DEFAULT_ORIGIN_OPTION),
                                        key="award_origin", accept_new_options=True,
                                        help="Pick from the list, or type any city or 3-letter airport code")
        with ac2:
            award_dest = st.selectbox("Destination", _AWPORTS, index=None,
                                      placeholder="Type a city or airport code…", key="award_dest",
                                      accept_new_options=True)
        with ac3:
            if award_mode == "One date":
                award_cabin = st.selectbox("Cabin", _CABIN_CHOICES, index=2, key="award_cabin")
            else:
                award_cabins = st.multiselect("Cabins", _CABIN_CHOICES, default=["ECONOMY", "BUSINESS"],
                                              key="award_cabins")

        from datetime import timedelta
        _today = date.today()
        _max_day = _today + timedelta(days=cash_quotes.MAX_LOOKAHEAD_DAYS)
        if award_mode == "One date":
            dc1, dc2 = st.columns([1, 2])
            with dc1:
                award_date = st.date_input("Departure date", key="award_date", min_value=_today)
            with dc2:
                flex_days = st.slider(
                    "Flexible +/- days", 0, 7, 0,
                    help="Widen the search window around the departure date to catch nearby saver space.",
                )
        elif award_mode == "Date range":
            award_range = st.date_input("Travel between", value=(_today + timedelta(days=30), _today + timedelta(days=90)),
                                        min_value=_today, max_value=_max_day, key="award_range")
        elif award_mode == "Outbound + return":
            rc1, rc2 = st.columns(2)
            with rc1:
                out_range = st.date_input("Outbound between", value=(_today + timedelta(days=60), _today + timedelta(days=75)),
                                          min_value=_today, max_value=_max_day, key="out_range")
            with rc2:
                ret_range = st.date_input("Return between", value=(_today + timedelta(days=67), _today + timedelta(days=85)),
                                          min_value=_today, max_value=_max_day, key="ret_range")
        else:
            st.caption("Searches every date from tomorrow to about 11 months out.")

        search_label = "Search award availability" if award_mode == "One date" else "Find the best deals"
        if st.button(search_label, type="primary"):
            o_code, _ = airports.resolve_input(award_origin)
            d_code, _ = airports.resolve_input(award_dest)
            if not award_origin or not award_dest:
                st.error("Pick both an origin and a destination.")
            elif not o_code or not d_code:
                bad = award_origin if not o_code else award_dest
                st.error(f"Couldn't recognize \"{bad}\" — try the 3-letter airport code instead (e.g. JFK).")
            elif o_code == d_code:
                st.error("Origin and destination are the same airport.")
            elif award_mode == "One date":
                st.session_state["award_offers"] = []
                st.session_state.pop("route_result", None)
                try:
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
            elif not award_cabins:
                st.error("Pick at least one cabin.")
            else:
                st.session_state["award_offers"] = []

                def _rng(v):
                    return (v[0], v[1]) if isinstance(v, (tuple, list)) and len(v) == 2 else (None, None)

                try:
                    with st.status("Finding the best deals…", expanded=True) as status:
                        if award_mode == "Outbound + return":
                            (os_, oe), (rs, re_) = _rng(out_range), _rng(ret_range)
                            if not all((os_, oe, rs, re_)):
                                raise ValueError("Pick a start and end date for both outbound and return.")
                            st.session_state["route_result"] = ("pair", route_search.search_pair(
                                o_code, d_code, award_cabins, os_, oe, rs, re_, log=status.write))
                        else:
                            lo, hi = _rng(award_range) if award_mode == "Date range" else (None, None)
                            if award_mode == "Date range" and not (lo and hi):
                                raise ValueError("Pick a start and end date.")
                            st.session_state["route_result"] = ("single", route_search.search(
                                o_code, d_code, award_cabins, lo, hi, log=status.write))
                        status.update(label="Done", state="complete", expanded=False)
                except (ValueError, seats_aero.NotConfigured, seats_aero.SearchFailed) as e:
                    st.error(str(e))

        route_result = st.session_state.get("route_result") if award_mode != "One date" else None
        if route_result:
            _render_route_result(*route_result)

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
                        "· which points to use is below"
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
               f"{_great:.1f}¢).")

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
    held = {name: miles for name, miles in ledger.load_program_balances().items()
            if any(partner.name == name for _, partner in matches)}
    for name, miles in held.items():
        if miles >= points_required:
            st.success(f"✅ You already hold **{miles:,}** {name} miles: enough to book this without "
                       "transferring anything. Spend those first.")
        else:
            st.info(f"You already hold **{miles:,}** {name} miles; transfer only "
                    f"**{points_required - miles:,.0f}** more.")
    ranked = rank_funding_pools(matches, max(points_required - sum(held.values()), 0), balances)

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
