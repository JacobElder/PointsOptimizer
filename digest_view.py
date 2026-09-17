"""Renders a Deal Finder digest (deal_digest.json or a fresh scan result)."""

from __future__ import annotations

import json

import streamlit as st

import deal_finder


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


def deal_card(d: dict, rank: int | None = None, bar: float | None = None, surplus: float | None = None) -> None:
    bar = bar if bar is not None else d["great_floor"]
    surplus = surplus if surplus is not None else d["surplus_usd"]
    with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            new = " 🆕" if d.get("new") else ""
            prefix = f"{rank}. " if rank else ""
            c1.markdown(f"**{prefix}{d['origin']} → {d['dest']}** · {d['cabin'].title()}{new}")
            c1.caption(
                f"{d['program']} · {d['date']} · {d['points']:,} pts + ${d['taxes_usd']:.0f} taxes · "
                f"{str(d['seats']) + ' seat(s)' if d['seats'] else 'seats not reported'}"
                f"{' · nonstop' if d.get('direct') else ''}"
                f"{' · ' + d['airlines'] if d.get('airlines') else ''}"
            )
            if d.get("bookable_now"):
                c1.caption(f"✅ Bookable now with the {d['held_miles']:,} miles you already hold")
            elif d.get("held_miles"):
                c1.caption(f"You hold {d['held_miles']:,}; transfer {d['top_up_needed']:,} more")
            approx = " (one-way fare from a date within 7 days)" if d.get("cash_is_approx") else ""
            vs_biz = " · first class valued against the business fare" if d["cabin"] == "FIRST" else ""
            basis = f" ({d['cash_basis']})" if d.get("cash_basis") else ""
            own = f" · award airline's own fare ${d['same_carrier_cash']:,.0f}" if d.get("same_carrier_cash") else ""
            c1.caption(f"Cash fare ${d['cash_price']:,.0f}{basis}{approx}{vs_biz}{own}")
            if d.get("other_dates"):
                c1.caption(f"Also {len(d['other_dates'])} other date(s): {', '.join(d['other_dates'][:8])}"
                           + (" …" if len(d["other_dates"]) > 8 else ""))
            if d.get("alternatives"):
                c1.caption("Alternatives: " + "; ".join(d["alternatives"]))
            if d.get("round_trip_half") is not None and d.get("one_way_cash"):
                c1.caption(f"One-way ${d['one_way_cash']:,.0f} · half of a 7-night round trip "
                           f"${d['round_trip_half']:,.0f}")
            c2.metric("CPP", f"{d['cpp']:.2f}¢")
            c2.caption(f"+${surplus:,.0f} above the {bar:.1f}¢ bar")


