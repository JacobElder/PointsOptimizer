"""
Shared UI for finding and valuing a return leg for an outbound award deal.

Used by both the Flight Analyzer (app.py) and the Deal Radar page. Given an
outbound deal, it searches seats.aero award space on the reverse route over a
return-date window, and (on explicit click) values each return option with a
live cash price -- plus a round-trip total when the outbound's own CPP/cash is
known. Every SerpApi lookup is behind a button so the scarce quota is only spent
on returns you choose to value.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

import check_alerts
import flight_search
import seats_aero

_CABINS = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]


def value_return(dep: str, arr: str, o) -> dict:
    """Live cash price + CPP for one return award option (one SerpApi call)."""
    try:
        offers = flight_search.search_cash_price(dep, arr, o.date, o.cabin, max_results=1)
    except flight_search.NotConfigured:
        return {"error": "SerpApi cash-price lookup isn't configured (SERPAPI_KEY)."}
    except flight_search.SearchFailed as e:
        return {"error": str(e)}
    if not offers:
        return {"error": "No cash price found for this return leg."}
    cash = offers[0].price_usd
    taxes_usd = o.taxes_fees * check_alerts._fx_rate(o.taxes_currency)
    cpp = (max(cash - taxes_usd, 0.0) / o.points) * 100 if o.points else None
    return {"cash": cash, "taxes_usd": taxes_usd, "cpp": cpp}


def render(outbound: dict, key: str) -> None:
    """Render the return-finder expander for an outbound deal.

    `outbound` needs origin, dest, date, cabin, points. If it also has cpp,
    cash_price and taxes_usd, a combined round-trip total is shown. `key`
    namespaces all widgets so multiple finders can coexist on one page.
    """
    dep = outbound["dest"]
    arr = outbound["origin"]
    with st.expander(f"🔁 Find a return flight ({dep} → {arr})"):
        if not seats_aero.is_configured():
            st.info("Add SEATS_AERO_API_KEY to enable live return-flight lookups.")
            return

        try:
            outbound_date = datetime.strptime(str(outbound.get("date")), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            outbound_date = datetime.utcnow().date()

        c1, c2 = st.columns(2)
        start = c1.date_input("Return between", value=outbound_date + timedelta(days=3), key=f"ret_start_{key}")
        end = c2.date_input("and", value=outbound_date + timedelta(days=14), key=f"ret_end_{key}")
        cabin_default = str(outbound.get("cabin", "")).upper()
        cabin = st.selectbox(
            "Cabin", _CABINS,
            index=_CABINS.index(cabin_default) if cabin_default in _CABINS else 2,
            key=f"ret_cabin_{key}",
        )

        offers_key = f"ret_offers_{key}"
        if st.button("Search returns", key=f"ret_go_{key}"):
            if end < start:
                st.warning("The end date is before the start date.")
                return
            with st.spinner(f"Searching {dep}→{arr} award space…"):
                try:
                    st.session_state[offers_key] = seats_aero.search_award_availability(
                        dep, arr, str(start), str(end), cabin=cabin, max_results=10
                    )
                except seats_aero.NotConfigured:
                    st.info("seats.aero API key not configured.")
                    return
                except seats_aero.SearchFailed as e:
                    st.error(str(e))
                    return

        offers = st.session_state.get(offers_key)
        if offers is None:
            return
        if not offers:
            st.warning(f"No {cabin.title()} award space found for {dep}→{arr} in that window.")
            return

        st.caption(f"{len(offers)} option(s), fewest points first:")
        for i, o in enumerate(offers):
            stops = "Nonstop" if o.direct else "Connection"
            seats_s = f"{o.remaining_seats} seat(s)" if o.remaining_seats else "seats: n/a"
            st.markdown(
                f"**{o.date}** · {o.program} · {o.airlines or '—'} — "
                f"**{o.points:,} pts** + {o.taxes_fees:.2f} {o.taxes_currency}"
            )
            st.caption(f"{stops} · {seats_s}")

            val_key = f"ret_val_{key}_{i}"
            if st.button("💵 Value this return (1 cash-price lookup)", key=f"ret_valbtn_{key}_{i}"):
                with st.spinner("Fetching cash price…"):
                    st.session_state[val_key] = value_return(dep, arr, o)

            val = st.session_state.get(val_key)
            if val is None:
                continue
            if val.get("error"):
                st.error(val["error"])
                continue

            rcpp = val["cpp"]
            st.markdown(f"↳ Return value: **{rcpp:.2f}¢/pt**" if rcpp is not None else "↳ Return value: n/a")
            st.caption(f"cash ${val['cash']:,.0f} − ${val['taxes_usd']:.0f} taxes over {o.points:,} pts")

            out_cpp = outbound.get("cpp")
            out_cash = outbound.get("cash_price")
            if rcpp is not None and out_cpp is not None and out_cash is not None:
                total_points = int(outbound["points"]) + o.points
                total_taxes = outbound.get("taxes_usd", 0.0) + val["taxes_usd"]
                total_cash = out_cash + val["cash"]
                trip_cpp = (max(total_cash - total_taxes, 0.0) / total_points) * 100 if total_points else None
                with st.container(border=True):
                    st.markdown(
                        f"**🧳 Round trip {arr}↔{dep}: {trip_cpp:.2f}¢/pt**"
                        if trip_cpp is not None else "**🧳 Round trip: n/a**"
                    )
                    st.caption(
                        f"{total_points:,} pts + ${total_taxes:.0f} taxes vs. ${total_cash:,.0f} cash · "
                        f"out {outbound['date']} ({out_cpp:.2f}¢) / back {o.date} ({rcpp:.2f}¢)"
                    )
