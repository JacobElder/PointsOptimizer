import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

import streamlit as st

import check_alerts
import deal_log
import flight_search
import seats_aero

st.set_page_config(page_title="Deal Radar — PointsOptimizer", page_icon="📡", layout="centered")

st.title("📡 Deal Radar")
st.caption(
    "A scheduled cloud routine checks your seats.aero alert emails every few hours, computes real "
    "CPP against a live cash price, and logs every deal it evaluates here. Most alerts clear "
    "seats.aero's own points/fee threshold but aren't actually good value — this is the needle-in-"
    "the-haystack view."
)

_CABINS = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]


def _value_return(dep: str, arr: str, o) -> dict:
    """Pull a live cash price for one return award option and compute its CPP.

    One SerpApi call per invocation -- only ever triggered by an explicit button
    click, to keep the 250/month cash-price quota under control.
    """
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


def _render_return_finder(d: dict) -> None:
    """Search seats.aero for award availability on the reverse route (the return leg)."""
    dep = d["dest"]
    arr = d["origin"]
    with st.expander(f"🔁 Find a return flight ({dep} → {arr})"):
        if not seats_aero.is_configured():
            st.info("Add SEATS_AERO_API_KEY to enable live return-flight lookups.")
            return

        try:
            outbound_date = datetime.strptime(d["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            outbound_date = datetime.utcnow().date()

        c1, c2 = st.columns(2)
        # Default to a typical 3–14 day trip length after the outbound date.
        start = c1.date_input(
            "Return between", value=outbound_date + timedelta(days=3), key=f"ret_start_{d['key']}"
        )
        end = c2.date_input(
            "and", value=outbound_date + timedelta(days=14), key=f"ret_end_{d['key']}"
        )
        cabin = st.selectbox(
            "Cabin", _CABINS,
            index=_CABINS.index(d["cabin"].upper()) if d["cabin"].upper() in _CABINS else 2,
            key=f"ret_cabin_{d['key']}",
        )

        offers_key = f"ret_offers_{d['key']}"
        if st.button("Search returns", key=f"ret_go_{d['key']}"):
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

            val_key = f"ret_val_{d['key']}_{i}"
            if st.button("💵 Value this return (1 cash-price lookup)", key=f"ret_valbtn_{d['key']}_{i}"):
                with st.spinner("Fetching cash price…"):
                    st.session_state[val_key] = _value_return(dep, arr, o)

            val = st.session_state.get(val_key)
            if val is None:
                continue
            if val.get("error"):
                st.error(val["error"])
                continue

            rcpp = val["cpp"]
            st.markdown(
                f"↳ Return value: **{rcpp:.2f}¢/pt**"
                if rcpp is not None else "↳ Return value: n/a"
            )
            st.caption(f"cash ${val['cash']:,.0f} − ${val['taxes_usd']:.0f} taxes over {o.points:,} pts")

            # Round-trip total: this return + the standout outbound.
            total_points = d["points"] + o.points
            total_taxes = d.get("taxes_usd", 0.0) + val["taxes_usd"]
            total_cash = (d.get("cash_price") or 0.0) + val["cash"]
            trip_cpp = (max(total_cash - total_taxes, 0.0) / total_points) * 100 if total_points else None
            with st.container(border=True):
                st.markdown(
                    f"**🧳 Round trip {arr}↔{dep}: {trip_cpp:.2f}¢/pt**"
                    if trip_cpp is not None else "**🧳 Round trip: n/a**"
                )
                st.caption(
                    f"{total_points:,} pts + ${total_taxes:.0f} taxes vs. ${total_cash:,.0f} cash · "
                    f"out {d['date']} ({d['cpp']:.2f}¢) / back {o.date} "
                    f"({rcpp:.2f}¢)" if rcpp is not None else ""
                )


data = deal_log.load()
deals = data.get("deals", [])

if not deals:
    st.info(
        "No deals logged yet. Once the scheduled routine runs, evaluated deals will show up here "
        "automatically — just refresh this page."
    )
    st.stop()

deals_by_cpp = sorted(deals, key=lambda d: d.get("cpp") if d.get("cpp") is not None else -1, reverse=True)
great = [d for d in deals_by_cpp if deal_log.is_great(d)]
last_checked = max((d.get("checked_at", "") for d in deals), default="")
best = deals_by_cpp[0] if deals_by_cpp and deals_by_cpp[0].get("cpp") is not None else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Deals evaluated", len(deals))
m2.metric("Standout deals", len(great))
m3.metric("Best CPP found", f"{best['cpp']:.2f}¢" if best else "—")
m4.metric("Last checked", last_checked.split("T")[0] if last_checked else "—")

st.divider()

if great:
    st.header(f"🎯 Act now — {len(great)} standout deal(s)")
    st.caption(
        "Best value first. These clear the cabin-aware bar (1.5¢/pt Economy, 2.0¢/pt Business/First) "
        "— book while they're still showing available."
    )
    for i, d in enumerate(great):
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            tag = " 🏆" if i == 0 else ""
            c1.markdown(f"**{d['origin']} → {d['dest']}**{tag}")
            c1.caption(f"{d['program']} · {d['cabin'].title()} · travel {d['date']}")
            if d.get("flight_number"):
                c1.caption(f"Flight {d['flight_number']}")
            c2.metric("CPP", f"{d['cpp']:.2f}¢")
            c2.caption(
                f"{d['points']:,} pts + ${d['taxes']:.2f} {d.get('currency', 'USD')}\n"
                f"vs. ${d['cash_price']:,.0f} cash"
            )
            if d.get("listing_url"):
                st.markdown(f"[View on seats.aero →]({d['listing_url']})")
            _render_return_finder(d)
    st.divider()

st.header("All evaluated deals")

SORT_OPTIONS = {
    "CPP (highest first)": (lambda d: d.get("cpp") if d.get("cpp") is not None else -1, True),
    "Travel date (soonest first)": (lambda d: d.get("date") or "9999-99-99", False),
    "Program / airline (A→Z)": (lambda d: (d.get("program") or "").lower(), False),
    "Points required (fewest first)": (lambda d: d.get("points") or 0, False),
    "Cash price (cheapest first)": (
        lambda d: d.get("cash_price") if d.get("cash_price") is not None else float("inf"),
        False,
    ),
    "Recently checked (newest first)": (lambda d: d.get("checked_at") or "", True),
}

filter_col, sort_col, reverse_col = st.columns([2, 2, 1])
with filter_col:
    cabins = sorted({d["cabin"] for d in deals if d.get("cabin")})
    cabin_filter = st.multiselect("Cabin", cabins, default=cabins)
with sort_col:
    sort_choice = st.selectbox("Sort by", list(SORT_OPTIONS.keys()))
with reverse_col:
    st.write("")  # vertical align with the selectboxes above
    reverse_extra = st.checkbox("Reverse")

verdicts = sorted({d["verdict"] for d in deals if d.get("verdict")})
verdict_filter = st.multiselect("Verdict", verdicts, default=verdicts)

key_fn, default_reverse = SORT_OPTIONS[sort_choice]
filtered = [d for d in deals if d.get("cabin") in cabin_filter and d.get("verdict") in verdict_filter]
filtered.sort(key=key_fn, reverse=default_reverse ^ reverse_extra)

st.caption(f"Showing {len(filtered)} of {len(deals)} deals")

BADGE = {"BOOK": "🟢", "BORDERLINE": "🟡", "SKIP": "🔴"}
for d in filtered:
    badge = BADGE.get(d.get("verdict"), "⚪")
    with st.container(border=True):
        cols = st.columns([3, 1])
        cols[0].markdown(
            f"{badge} **{d['origin']}→{d['dest']}** · {d['program']} · {d['cabin'].title()} · {d['date']}"
        )
        cpp_s = f"{d['cpp']:.2f}¢" if d.get("cpp") is not None else "N/A"
        cols[1].markdown(f"### {cpp_s}")
        cash_s = f"${d['cash_price']:,.0f}" if d.get("cash_price") is not None else "no cash price"
        flight_s = f" · Flight {d['flight_number']}" if d.get("flight_number") else ""
        st.caption(
            f"{d['points']:,} pts + ${d['taxes']:.2f} {d.get('currency', 'USD')} taxes · "
            f"{cash_s} · checked {d.get('checked_at', '?')}{flight_s}"
        )
