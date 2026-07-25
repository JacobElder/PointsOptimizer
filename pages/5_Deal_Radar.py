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
    "A cloud routine logs your seats.aero alert emails here (free — no pricing). Pricing is "
    "**on-demand**: click *Price this deal* on any captured deal to spend one live cash-price "
    "lookup and get its real CPP, so your SerpApi quota only goes to deals you actually care about."
)

_CABINS = ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]


def _price_pending(deal: dict) -> dict:
    """Live cash price + CPP for one captured (unpriced) deal, on demand.

    One SerpApi call per click -- the whole point of the on-demand model is that
    the scarce 250/month quota is spent only on deals you choose to price.
    """
    try:
        offers = flight_search.search_cash_price(
            deal["origin"], deal["dest"], deal["date"], deal["cabin"], max_results=1
        )
    except flight_search.NotConfigured:
        return {"error": "SerpApi cash-price lookup isn't configured (SERPAPI_KEY)."}
    except flight_search.SearchFailed as e:
        return {"error": str(e)}
    if not offers:
        return {"error": "No cash price found for this route/date/cabin."}
    cash = offers[0].price_usd
    taxes_usd = float(deal.get("taxes") or 0) * check_alerts._fx_rate(deal.get("currency", "USD"))
    points = int(deal.get("points") or 0)
    cpp = (max(cash - taxes_usd, 0.0) / points) * 100 if points else None
    verdict = deal_log.verdict_for(cpp, deal.get("cabin", ""))
    return {"cash": cash, "taxes_usd": taxes_usd, "cpp": cpp, "verdict": verdict}


def _pending_id(p: dict, idx: int) -> str:
    """Stable per-deal id for widget keys / session state."""
    if p.get("message_id"):
        return str(p["message_id"])
    try:
        return deal_log.make_key(p["program"], p["origin"], p["dest"], p["cabin"], p["date"], p["points"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return f"idx{idx}"


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
pending = data.get("pending", [])

if not deals and not pending:
    st.info(
        "No deals logged yet. Once the capture routine runs, seats.aero alerts will show up here "
        "for you to price on demand — just refresh this page."
    )
    st.stop()

deals_by_cpp = sorted(deals, key=lambda d: d.get("cpp") if d.get("cpp") is not None else -1, reverse=True)
great = [d for d in deals_by_cpp if deal_log.is_great(d)]
best = deals_by_cpp[0] if deals_by_cpp and deals_by_cpp[0].get("cpp") is not None else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Captured (unpriced)", len(pending))
m2.metric("Priced", len(deals))
m3.metric("Standouts", len(great))
m4.metric("Best CPP", f"{best['cpp']:.2f}¢" if best else "—")

st.divider()

# ── Captured deals: price on demand ─────────────────────────────────────────
if pending:
    st.header(f"📋 Captured — {len(pending)} awaiting your call")
    st.caption("Filter, then click *Price this deal* to spend one live cash-price lookup on it.")

    fcol, scol = st.columns(2)
    pcabins = sorted({p.get("cabin", "") for p in pending if p.get("cabin")})
    with fcol:
        pcabin_filter = st.multiselect("Cabin", pcabins, default=pcabins, key="pend_cabin")
    with scol:
        PEND_SORT = {
            "Travel date (soonest first)": lambda p: p.get("date") or "9999-99-99",
            "Points (fewest first)": lambda p: int(p.get("points") or 0),
            "Program (A→Z)": lambda p: (p.get("program") or "").lower(),
            "Cabin": lambda p: p.get("cabin", ""),
        }
        pend_sort = st.selectbox("Sort by", list(PEND_SORT.keys()), key="pend_sort")

    shown = [p for p in pending if p.get("cabin") in pcabin_filter]
    shown.sort(key=PEND_SORT[pend_sort])
    st.caption(f"Showing {len(shown)} of {len(pending)} captured deals")

    for idx, p in enumerate(shown):
        pid = _pending_id(p, idx)
        with st.container(border=True):
            top = st.columns([3, 1])
            top[0].markdown(f"**{p.get('origin', '?')} → {p.get('dest', '?')}**")
            top[0].caption(
                f"{p.get('program', '?')} · {str(p.get('cabin', '')).title()} · travel {p.get('date', '?')}"
            )
            if p.get("flight_number"):
                top[0].caption(f"Flight {p['flight_number']}")
            top[1].markdown(f"### {int(p.get('points') or 0):,}")
            top[1].caption(f"pts + {float(p.get('taxes') or 0):.0f} {p.get('currency', 'USD')}")
            if p.get("listing_url"):
                st.markdown(f"[View on seats.aero →]({p['listing_url']})")

            res_key = f"pend_priced_{pid}"
            if st.button("💵 Price this deal (1 cash-price lookup)", key=f"pend_btn_{pid}"):
                with st.spinner("Fetching live cash price…"):
                    st.session_state[res_key] = _price_pending(p)

            res = st.session_state.get(res_key)
            if res is None:
                continue
            if res.get("error"):
                st.error(res["error"])
                continue

            cpp = res["cpp"]
            badge = {"BOOK": "🟢", "BORDERLINE": "🟡", "SKIP": "🔴"}.get(res["verdict"], "⚪")
            r1, r2 = st.columns([3, 1])
            r1.markdown(f"{badge} **{res['verdict']}**")
            r1.caption(
                f"cash ${res['cash']:,.0f} − ${res['taxes_usd']:.0f} taxes over {int(p.get('points') or 0):,} pts"
            )
            r2.metric("CPP", f"{cpp:.2f}¢" if cpp is not None else "n/a")

            if cpp is not None:
                enriched = {**p, "key": pid, "cpp": cpp,
                            "cash_price": res["cash"], "taxes_usd": res["taxes_usd"]}
                _render_return_finder(enriched)
    st.divider()

if not deals:
    st.stop()

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

st.header("Previously priced deals")

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
