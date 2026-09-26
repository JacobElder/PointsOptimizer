"""Renders a Deal Finder digest (deal_digest.json or a fresh scan result)."""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

import deal_finder
import funding
import ledger
import places
import seats_aero
import valuation


def load_digest() -> dict | None:
    try:
        with open(deal_finder.DIGEST_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def render_digest(digest: dict | None = None) -> None:
    digest = digest if digest is not None else load_digest()
    if not digest:
        st.info("No deal scan yet. Click **Scan now** above, or wait for the daily scan.")
        return
    top = digest.get("top", [])
    scan = digest.get("scan", {})
    held = digest.get("held_miles", [])
    if held:
        st.header("✅ Book now with miles you already hold")
        st.caption("Your airline balances covered these when the scan ran: nothing to transfer. "
                   "If the payment line below still asks for a transfer, this app can't see your "
                   "balances — check the Wallet page.")
        for d in held:
            deal_card(d)
    for group in digest.get("watchlist", []):
        st.header(f"⭐ Watchlist: {group.get('label', 'Watchlist')}")
        if not group.get("deals"):
            if not group.get("matched_awards"):
                st.caption("No award seats found for these dates — seats.aero may not cover this "
                           "route, or the dates may fall outside the scan window.")
            else:
                st.caption(f"Nothing clears this entry's bar yet ({group['matched_awards']:,} matching "
                           f"awards, {group.get('priced', 0)} priced).")
        for d in group.get("deals") or []:
            # Only the bar is watchlist-specific. watch_surplus_usd measures against
            # that bar, and the card's sentence compares with the program's baseline.
            deal_card(d, bar=d.get("watch_bar"))
    st.header(f"🏆 Top {len(top)} of {scan.get('candidates', 0):,} awards")
    programs = ", ".join(sorted(seats_aero.SOURCE_TO_PARTNER.get(x, x) for x in scan.get("sources", [])))
    st.caption(
        f"Programs scanned: {programs}. "
        "Ranked by dollars of value above what each program's points are normally worth, using live "
        "Google Flights cash fares, after discounts for long routings, mixed cabins, unconfirmed "
        "flights and stale data. At most 6 deals per program in economy and 6 in business or first, "
        "and the last few places are held for economy. One entry per destination + cabin; other "
        "dates, origins and programs are listed under it. Award space moves fast: re-check on "
        "seats.aero before transferring."
    )
    drift = digest.get("estimate_drift") or {}
    if drift and {"median_pct", "p90_pct", "n"} <= set(drift):
        ok = drift["median_pct"] <= deal_finder.ESTIMATE_DRIFT_WARN_PCT
        st.caption(("✅ " if ok else "⚠️ ") + f"Fare estimates (used to choose what to price) were "
                   f"{drift['median_pct']}% off real fares this run, {drift['p90_pct']}% at the 90th "
                   f"percentile, over {drift['n']} lookups."
                   + ("" if ok else " That's high: the deals picked for pricing may be poorly chosen."))
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
    """One deal: verdict headline, then flight / how to pay / dates, then links.

    Tolerant of partial data: a deal missing fields costs its own card, never the page.
    """
    try:
        _deal_card(d, rank, bar, surplus)
    except Exception as e:  # noqa: BLE001 - one bad row must not blank the page
        st.warning(f"Couldn't show {d.get('origin', '?')} → {d.get('dest', '?')} ({type(e).__name__}).")


def _deal_card(d: dict, rank: int | None, bar: float | None, surplus: float | None) -> None:
    safe = places.md_safe
    cpp = d.get("cpp")
    points = int(d.get("points") or 0)
    cash = d.get("cash_price")
    taxes = float(d.get("taxes_usd") or 0)
    cabin = str(d.get("cabin") or "").replace("_", " ").title() or "Unknown cabin"
    bar = (bar if bar is not None else d.get("great_floor")
           or valuation.great_floor(d.get("cabin", ""), d.get("program", "")))
    surplus = surplus if surplus is not None else d.get("surplus_usd")
    trip = d.get("trip") or {}
    with st.container(border=True):
        title = (f"{rank}. " if rank else "") + \
            f"{places.airport_label(d.get('origin', '?'))} → {places.airport_label(d.get('dest', '?'))}"
        st.markdown(f"#### {safe(title)}")
        st.caption(" · ".join(safe(t) for t in [cabin, d.get("program", "")] + (["🆕 new"] if d.get("new") else []) if t))

        # One headline line: works on a phone, and says what the numbers mean.
        program = d.get("program", "")
        # Judge against the bar this card actually shows. Recomputing from valuation
        # here disagreed with the stored floor whenever the bars were retuned after
        # the digest was written.
        if cpp is None:
            verdict = ""
        elif cpp >= bar:
            verdict = "BOOK"
        else:
            verdict = "BORDERLINE" if cpp >= bar * 0.8 else "SKIP"
        mark = {"BOOK": "🟢 Book", "BORDERLINE": "🟡 Borderline", "SKIP": "🔴 Skip"}.get(verdict, "")
        lead = f"{mark} · " if mark else ""
        bits = [f"**{cpp:.2f}¢ per point**" if cpp is not None else "no value yet",
                (f"{points:,} points + taxes not reported" if d.get("taxes_unknown")
                 else f"{points:,} points + about ${taxes:,.0f} taxes (estimated)"
                 if d.get("taxes_estimated")
                 else f"{points:,} points + ${taxes:,.0f} taxes") if points else None,
                f"vs a ${cash:,.0f} cash fare" if cash else None]
        st.markdown(safe(lead + " · ".join(b for b in bits if b)))
        baseline = d.get("baseline_cpp") or valuation.baseline_cpp(program, d.get("cabin", ""))
        if surplus is not None and cpp is not None:
            st.markdown(safe(f"**${surplus:,.0f} better** than spending these points the usual way "
                             f"(about {baseline:.2f}¢ each; we only flag {program} above {bar:.2f}¢)"))
        if d.get("history_pct") is not None and d.get("history_days", 0) >= 10:
            pct, days = d["history_pct"], d["history_days"]
            st.caption("📉 " + ("Cheapest this route has been in the last "
                                f"{days} days" if pct >= 0.99 else
                                f"Cheaper than {pct:.0%} of the last {days} days on this route"
                                if pct >= 0.5 else
                                f"Pricier than usual: {1 - pct:.0%} of the last {days} days were cheaper"))
        if d.get("rank_notes"):
            st.caption("⚖️ Ranked lower because " + safe("; ".join(d["rank_notes"])))
        own = d.get("same_carrier_cash")
        if own and cash and own < cash and points:
            carrier = places.airline_names(d.get("airlines") or "").split(",")[0] or "that airline"
            st.caption(safe(f"↘️ On {carrier} itself the fare is ${own:,.0f}, which would make this "
                            f"{(own - taxes) / points * 100:.2f}¢/pt."))
        if d.get("round_trip_half") is not None and d.get("one_way_cash") and cash and cash < d["one_way_cash"]:
            st.caption(safe(f"The one-way fare on this date is ${d['one_way_cash']:,.0f}."))
        # cash_basis already says whether a round trip was priced, was cheaper, or
        # wasn't checked, so it isn't repeated here.
        st.caption(safe(f"Cash fare: {d.get('cash_basis') or 'cheapest comparable fare'}"
                        + (" (borrowed from a nearby date, not this one)" if d.get("cash_is_approx") else "")
                        + (" · first class compared with the business fare" if d.get("cabin") == "FIRST" else "")))

        left, right = st.columns([3, 2], gap="medium")
        with left:
            st.markdown("**✈️ Flight**")
            date_line = places.nice_date(d.get("date", ""))
            if trip:
                stops = int(trip.get("stops") or 0)
                via = f" via {', '.join(places.city(c) for c in trip.get('connections') or [])}" if stops else ""
                plus = _days_later(trip.get("departs_at", ""), trip.get("arrives_at", ""))
                date_line += (f" · {_hm(trip.get('departs_at', ''))} → {_hm(trip.get('arrives_at', ''))}{plus}"
                              f" · {_duration(trip.get('duration_min'))} · "
                              + ("Nonstop" if not stops else f"{stops} stop{'s' if stops > 1 else ''}{via}"))
                st.write(safe(date_line))
                st.caption(safe(" · ".join(trip.get("flights") or [])
                                + (f" · {trip['carriers']}" if trip.get("carriers") else "")))
                if trip.get("mixed_cabin"):
                    st.warning(safe("Mixed cabin: " + ", ".join(trip.get("lower_cabin_legs") or [])
                                    + ". Worth less than the value above."), icon="⚠️")
                if trip.get("airport_changes"):
                    st.warning("Airport change mid-trip: " + ", ".join(trip["airport_changes"])
                               + ". You'd have to get between airports yourself.", icon="🚕")
                if d.get("slow"):
                    st.caption("🐢 Much longer than flying there directly.")
                if trip.get("seats"):
                    st.caption(f"{trip['seats']} seat{'s' if trip['seats'] != 1 else ''} left when we checked")
            else:
                st.write(safe(date_line))
                if d.get("airlines"):
                    st.caption(safe(places.airline_names(d["airlines"])))
                if d.get("trip_unverified"):
                    st.caption("⚠️ seats.aero didn't answer when we re-checked this one.")
                if d.get("unverified"):
                    # Without trip details the stop count is unknown: seats.aero's
                    # `direct` flag only means "one flight number".
                    st.caption("⚠️ Flights, stops and seats not confirmed — check on seats.aero "
                               "before you transfer.")
            if d.get("other_dates"):
                st.markdown("**📅 Also available**")
                st.caption(", ".join(places.nice_date(x, weekday=False) for x in d["other_dates"][:12])
                           + (f" and {len(d['other_dates']) - 12} more" if len(d["other_dates"]) > 12 else "")
                           + (" — on this many dates it's standard pricing, not a flash sale."
                              if len(d["other_dates"]) > 30 else ""))
            ret = d.get("return_option")
            if ret:
                st.markdown("**↩️ Return**")
                st.caption(safe(f"{places.nice_date(ret['date'])} for {ret['points']:,} points + "
                                f"${ret['taxes_usd']:,.0f} — {ret['round_trip_points']:,} points round trip "
                                f"on {ret['program']}"))
            elif d.get("points") and (d.get("cabin") in ("BUSINESS", "FIRST") or d["points"] > 30000):
                st.caption("↩️ One way only — no matching return award turned up in this scan.")
            if d.get("alternatives"):
                st.markdown("**🔁 Other ways**")
                st.caption(safe(" · ".join(d["alternatives"])))
        with right:
            st.markdown("**💳 How to pay**")
            balances, programs = _balances()
            plan = funding.plan(d.get("program", ""), points, balances, programs)
            st.write(safe(plan.summary))
            if plan.covered_by_held:
                st.caption("No transfer needed.")
            elif plan.pools:
                st.caption("Transfers are irreversible: confirm the award on the airline's site first.")

        links = []
        if trip.get("booking_url"):
            links.append(f"[{safe(trip.get('booking_label') or 'Book')} →]({trip['booking_url']})")
        from urllib.parse import quote
        gf = ("https://www.google.com/travel/flights?q="
              + quote(f"Flights from {d.get('origin', '')} to {d.get('dest', '')} on {d.get('date', '')} one way"))
        links.append(f"[Check the cash fare on Google Flights →]({gf})")
        age = d.get("age_days")
        if age is None:
            age_s = ""
        elif age < 1:
            age_s = " · award seen today"
        elif age <= 5:
            shown = d.get("age_shown", int(age))
            age_s = f" · award seen {shown} day{'s' if shown != 1 else ''} ago, usually still there"
        else:
            shown = d.get("age_shown", int(age))
            age_s = f" · award seen {shown} days ago, may be gone: check before transferring"
        st.caption(" · ".join(links) + age_s)
