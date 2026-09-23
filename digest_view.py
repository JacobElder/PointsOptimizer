"""Renders a Deal Finder digest (deal_digest.json or a fresh scan result)."""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

import deal_finder
import funding
import ledger
import places
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
        st.caption("Your airline balances already cover these: nothing to transfer.")
        for d in held:
            deal_card(d)
    for group in digest.get("watchlist", []):
        st.header(f"⭐ Watchlist: {group.get('label', 'Watchlist')}")
        if not group.get("deals"):
            st.caption(f"Nothing clears this entry's bar yet ({group.get('matched_awards', 0):,} matching "
                       f"awards, {group.get('priced', 0)} priced).")
        for d in group.get("deals") or []:
            deal_card(d, bar=d.get("watch_bar"), surplus=d.get("watch_surplus_usd"))
    st.header(f"🏆 Top {len(top)} of {scan.get('candidates', 0):,} awards")
    st.caption(
        f"Programs: {', '.join(scan.get('sources', []))}. "
        "Ranked by dollars of value above what each program's points are normally worth, after "
        "discounts for long routings, mixed cabins and stale data. At most 6 deals per program per "
        "cabin, and the last few places are held for economy. "
        "using live Google Flights cash fares. One entry per destination + cabin; other dates, "
        "origins and programs are listed under it. Award space moves fast: re-check on seats.aero."
    )
    drift = digest.get("estimate_drift") or {}
    if drift:
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
    bar = bar if bar is not None else d.get("great_floor") or valuation.great_floor(d.get("cabin", ""))
    surplus = surplus if surplus is not None else d.get("surplus_usd")
    trip = d.get("trip") or {}
    with st.container(border=True):
        title = (f"{rank}. " if rank else "") + \
            f"{places.airport_label(d.get('origin', '?'))} → {places.airport_label(d.get('dest', '?'))}"
        st.markdown(f"#### {safe(title)}")
        st.caption(" · ".join(safe(t) for t in [cabin, d.get("program", "")] + (["🆕 new"] if d.get("new") else []) if t))

        # One headline line: works on a phone, and says what the numbers mean.
        program = d.get("program", "")
        if d.get("watch_bar") and cpp is not None:  # judged against this entry's own bar
            verdict = "BOOK" if cpp >= d["watch_bar"] else "BORDERLINE"
        else:
            verdict = valuation.verdict_for(cpp, d.get("cabin", ""), program)
        mark = {"BOOK": "🟢 Book", "BORDERLINE": "🟡 Borderline", "SKIP": "🔴 Skip"}.get(verdict, "")
        bits = [f"**{cpp:.2f}¢ per point**" if cpp is not None else "no value yet",
                f"{points:,} points + ${taxes:,.0f} taxes" if points else None,
                f"vs a ${cash:,.0f} cash fare" if cash else None]
        st.markdown(safe(f"{mark} · " + " · ".join(b for b in bits if b)))
        baseline = d.get("baseline_cpp") or valuation.baseline_cpp(program, d.get("cabin", ""))
        if surplus is not None and cpp is not None:
            st.markdown(safe(f"**${surplus:,.0f} better** than spending these points the usual way "
                             f"(about {baseline:.2f}¢ each; we only flag {program} above {bar:.2f}¢)"))
        if d.get("rank_notes"):
            st.caption("⚖️ Ranked lower because " + safe("; ".join(d["rank_notes"])))
        st.caption(safe(f"Cash fare: {d.get('cash_basis') or 'cheapest comparable fare'}"
                        + (" (from a date within 7 days)" if d.get("cash_is_approx") else "")
                        + (" · first class compared with the business fare" if d.get("cabin") == "FIRST" else "")
                        + (" · no round trip could be priced, so this is the one-way fare and may flatter the deal"
                           if d.get("rt_unavailable") else "")))

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
                if d.get("trip_unverified"):
                    st.caption("⚠️ Couldn't re-check this one with seats.aero just now.")
            else:
                st.write(safe(date_line))
            if d.get("other_dates"):
                st.markdown("**📅 Also available**")
                st.caption(", ".join(places.nice_date(x, weekday=False) for x in d["other_dates"][:12])
                           + (f" and {len(d['other_dates']) - 12} more" if len(d["other_dates"]) > 12 else "")
                           + (" — on this many dates it's standard pricing, not a flash sale."
                              if len(d["other_dates"]) > 30 else ""))
            if d.get("points") and (d.get("cabin") in ("BUSINESS", "FIRST") or d["points"] > 30000):
                st.caption(f"↩️ One way only — a return would cost roughly another {d['points']:,} points.")
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
            age_s = f" · award seen {age:.0f} day{'s' if age >= 1.5 else ''} ago, usually still there"
        else:
            age_s = f" · award seen {age:.0f} days ago, may be gone: check before transferring"
        st.caption(" · ".join(links) + age_s)
