"""
Sends the Deal Finder digest email via Gmail SMTP with an App Password (not
OAuth) -- simplest option for an unattended scheduled job.

Configure via environment variables or Streamlit secrets:
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD
Generate an App Password at https://myaccount.google.com/apppasswords
(requires 2-Step Verification enabled on the account).
"""

from __future__ import annotations

import html as html_lib
import os
import smtplib
from email.message import EmailMessage

class NotConfigured(Exception):
    """Raised when Gmail credentials aren't set."""


def _get_credentials() -> tuple[str, str]:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        try:
            import streamlit as st

            address = address or st.secrets.get("GMAIL_ADDRESS")
            app_password = app_password or st.secrets.get("GMAIL_APP_PASSWORD")
        except Exception:
            pass
    if not address or not app_password:
        raise NotConfigured(
            "Not configured. Generate a Gmail App Password at "
            "https://myaccount.google.com/apppasswords (needs 2-Step Verification on), "
            "then set GMAIL_ADDRESS and GMAIL_APP_PASSWORD as environment variables or in "
            ".streamlit/secrets.toml."
        )
    return address, app_password


def is_configured() -> bool:
    try:
        _get_credentials()
        return True
    except NotConfigured:
        return False


# ── Deal Finder digest email ─────────────────────────────────────────────────
# Table-based inline-styled HTML: the only layout Gmail/Outlook/iOS Mail all render.

_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def _esc(v) -> str:
    return html_lib.escape(str(v))


def _google_flights_url(origin: str, dest: str, date: str) -> str:
    from urllib.parse import quote
    return "https://www.google.com/travel/flights?q=" + quote(f"Flights from {origin} to {dest} on {date} one way")


def _chip(text: str, bg: str, fg: str) -> str:
    return (f'<span style="display:inline-block; background:{bg}; color:{fg}; font-size:12px; font-weight:600; '
            f'padding:3px 9px; border-radius:999px; margin:0 6px 8px 0;">{_esc(text)}</span>')


def _row(label: str, value_html: str) -> str:
    return (f'<tr><td valign="top" style="padding:5px 12px 5px 0; width:92px; font-size:12px; color:#6b7280; '
            f'text-transform:uppercase; letter-spacing:.04em;">{_esc(label)}</td>'
            f'<td valign="top" style="padding:5px 0; font-size:14px; color:#111827; line-height:20px;">{value_html}</td></tr>')


def _digest_card(d: dict) -> str:
    import places

    chips = ""
    if d.get("bookable_now"):
        chips += _chip(f"✅ Book with the {d['held_miles']:,} miles you have", "#dcfce7", "#166534")
    elif d.get("held_miles"):
        chips += _chip(f"You have {d['held_miles']:,} · transfer {d['top_up_needed']:,} more", "#fef9c3", "#854d0e")
    if d.get("watch_label"):
        chips += _chip(f"⭐ {d['watch_label']}", "#e0e7ff", "#3730a3")
    if d.get("new") is False:
        chips += _chip("Sent before", "#f3f4f6", "#4b5563")

    cabin = d["cabin"].replace("_", " ").title()
    # seats.aero's `direct` flag only means "one flight number" -- EWR-JNB is
    # flagged direct and stops twice -- so without trip details the stop count is
    # genuinely unknown and saying "Nonstop" would be a guess presented as fact.
    stops = "Stops not confirmed"
    if d.get("trip"):
        n = len(d["trip"].get("connections") or [])
        stops = "Nonstop" if not n else f"{n} stop{'s' if n > 1 else ''}"
    bar = d.get("watch_bar", d["great_floor"])
    # The sentence below compares with the program's BASELINE value, so the dollar
    # figure has to be the baseline surplus. watch_surplus_usd measures against the
    # watchlist bar instead and is for ranking only: printing it here told the user
    # a $930 deal was worth $33.
    surplus = d["surplus_usd"]

    trip = d.get("trip") or {}
    if trip.get("mixed_cabin"):
        chips += _chip("⚠️ Mixed cabin: " + ", ".join(trip["lower_cabin_legs"]), "#fee2e2", "#991b1b")
    if trip.get("airport_changes"):
        chips += _chip("🚕 Airport change: " + ", ".join(trip["airport_changes"]), "#fee2e2", "#991b1b")
    if d.get("slow"):
        chips += _chip("🐢 Long itinerary", "#fef3c7", "#92400e")
    shown = d.get("age_shown")
    if shown is None and d.get("age_days") is not None:
        shown = int(d["age_days"])
    if shown is not None and shown > 5:
        chips += _chip(f"Last confirmed {shown} days ago — check before you transfer", "#f3f4f6", "#4b5563")
    if d.get("unverified"):
        chips += _chip("⚠️ Flights and seats not confirmed — check before you transfer", "#fef3c7", "#92400e")
    if d.get("rt_unavailable"):
        chips += _chip("No round-trip fare to compare against", "#fef3c7", "#92400e")

    if d.get("taxes_unknown"):
        taxes = "taxes not reported — check them on the airline's site"
    elif d.get("taxes_estimated"):
        taxes = (f"about ${d['taxes_usd']:,.0f} taxes &amp; fees "
                 f"<span style='color:#92400e'>(estimated — seats.aero reported none)</span>")
    else:
        taxes = f"${d['taxes_usd']:,.0f} taxes &amp; fees"
    rows = _row("Award", f"<b>{d['points']:,} points</b> + {taxes}")
    fare = f"<b>${d['cash_price']:,.0f}</b> <span style='color:#6b7280'>· {_esc(d.get('cash_basis') or 'cash fare')}</span>"
    extras = []
    if d.get("round_trip_half") is not None and d.get("one_way_cash") and d["cash_price"] < d["one_way_cash"]:
        extras.append(f"one-way ${d['one_way_cash']:,.0f}")
    own = d.get("same_carrier_cash")
    if own and d.get("airlines"):
        carrier = places.airline_names(d["airlines"]).split(",")[0]
        extras.append(f"on {_esc(carrier)} itself: ${own:,.0f}"
                      + (f", which would make this {(own - d['taxes_usd']) / d['points'] * 100:.2f}&cent;/pt"
                         if own < d["cash_price"] else ""))
    if d["cabin"] == "FIRST":
        extras.append("first class compared with the business fare")
    if d.get("cash_is_approx"):
        extras.append("fare borrowed from a nearby date")
    if extras:
        fare += f"<br><span style='font-size:12px; color:#6b7280'>{' · '.join(extras)}</span>"
    rows += _row("Cash fare", fare)
    baseline = d.get("baseline_cpp")
    value_html = (f"<span style='color:#15803d; font-weight:600'>${surplus:,.0f} better</span> than spending "
                  + (f"these points the usual way (about {baseline:.2f}&cent; each)"
                     if baseline else "them the usual way")
                  + f"<br><span style='font-size:12px; color:#6b7280'>We only flag {_esc(d['program'])} "
                    f"above {bar:.2f}&cent;/pt</span>")
    if d.get("history_pct") is not None and d["history_days"] >= 10:
        pct, days = d["history_pct"], d["history_days"]
        phrase = ("the cheapest this route has been in the last "
                  f"{days} days" if pct >= 0.99 else
                  f"cheaper than {pct:.0%} of the last {days} days on this route" if pct >= 0.5 else
                  f"pricier than usual for this route ({1 - pct:.0%} of days were cheaper)")
        value_html += f"<br><span style='font-size:12px; color:#6b7280'>Price history: {phrase}</span>"
    if d.get("rank_notes"):
        value_html += (f"<br><span style='font-size:12px; color:#92400e'>Ranked lower because "
                       f"{_esc('; '.join(d['rank_notes']))}</span>")
    rows += _row("Value", value_html)
    ret = d.get("return_option")
    if ret:
        rows += _row("Return", f"{_esc(places.nice_date(ret['date']))} for <b>{ret['points']:,} points</b>"
                               f" + ${ret['taxes_usd']:,.0f}<br>"
                               f"<span style='font-size:12px; color:#6b7280'>"
                               f"{ret['round_trip_points']:,} points round trip on {_esc(ret['program'])}</span>")
    elif d["cabin"] in ("BUSINESS", "FIRST") or d["points"] > 30000:
        rows += _row("Return", "No matching return award found in this scan — this is a one way")
    dates = f"<b>{places.nice_date(d['date'])}</b>"
    others = d.get("other_dates") or []
    if others:
        shown = ", ".join(places.nice_date(x, weekday=False) for x in others[:6])
        more = f" and {len(others) - 6} more" if len(others) > 6 else ""
        note = ("<br>Available on this many dates means it's standard pricing, not a flash sale — no rush."
                if len(others) > 30 else "")
        dates += f"<br><span style='font-size:12px; color:#6b7280'>Also: {shown}{more}{note}</span>"
    rows += _row("Dates", dates)
    if trip and trip.get("seats"):
        rows += _row("Seats", f"{trip['seats']} left when we checked")
    if trip:
        n_stops = len(trip.get("connections") or [])
        h, m = divmod(int(trip.get("duration_min") or 0), 60)
        via = f" via {', '.join(places.city(c) for c in trip['connections'])}" if n_stops else ""
        rows += _row("Flights", f"{_esc(' · '.join(trip.get('flights') or []))}<br>"
                                f"<span style='font-size:12px; color:#6b7280'>{h}h {m:02d}m · "
                                f"{'Nonstop' if not n_stops else f'{n_stops} stop' + ('s' if n_stops > 1 else '') + _esc(via)}"
                                f" · {_esc(trip.get('carriers') or '')}</span>")
    elif d.get("airlines"):
        rows += _row("Airlines", _esc(places.airline_names(d["airlines"])))
    if d.get("pay_summary"):
        rows += _row("Pay with", _esc(d["pay_summary"]))
    if d.get("alternatives"):
        rows += _row("Also", "<br>".join(_esc(a) for a in d["alternatives"]))

    link = _google_flights_url(d["origin"], d["dest"], d["date"])
    book = ""
    if trip.get("booking_url"):
        book = (f'<a href="{_esc(trip["booking_url"])}" style="display:inline-block; background:#2563eb; color:#ffffff; '
                f'font-size:13px; font-weight:700; text-decoration:none; padding:8px 14px; border-radius:8px; '
                f'margin:0 12px 6px 0;">{_esc(trip.get("booking_label") or "Book")} &rarr;</a>')
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #e5e7eb; border-radius:12px; margin:0 0 16px; background:#ffffff;">
      <tr><td style="padding:18px 20px 16px;">
        {f'<div>{chips}</div>' if chips else ''}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td valign="top" style="font-family:{_FONT};">
              <div style="font-size:17px; font-weight:700; color:#111827; line-height:23px;">
                {_esc(places.airport_label(d['origin']))} &rarr; {_esc(places.airport_label(d['dest']))}
              </div>
              <div style="font-size:13px; color:#4b5563; margin-top:3px;">
                {_esc(d['program'])} &middot; {cabin} &middot; {stops}
              </div>
            </td>
            <td valign="top" align="right" style="font-family:{_FONT}; white-space:nowrap; padding-left:12px;">
              <div style="font-size:26px; font-weight:800; color:#15803d; line-height:28px;">{d['cpp']:.2f}&cent;</div>
              <div style="font-size:11px; color:#6b7280;">per point</div>
            </td>
          </tr>
        </table>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="margin-top:12px; border-top:1px solid #f3f4f6; font-family:{_FONT};">
          {rows}
        </table>
        <div style="margin-top:12px; font-family:{_FONT};">
          {book}<a href="{link}" style="font-size:13px; color:#2563eb; text-decoration:none; font-weight:600;">
            Check the cash fare on Google Flights &rarr;</a>
        </div>
      </td></tr>
    </table>"""


def _digest_text(d: dict) -> str:
    import places
    trip = d.get("trip") or {}
    if trip:
        n = len(trip.get("connections") or [])
        stops = "Nonstop" if not n else f"{n} stop{'s' if n > 1 else ''}"
    else:
        stops = "stops not confirmed"  # seats.aero's `direct` flag is not a stop count
    taxes = ("taxes not reported" if d.get("taxes_unknown")
             else f"about ${d['taxes_usd']:,.0f} taxes (estimated)" if d.get("taxes_estimated")
             else f"${d['taxes_usd']:,.0f} taxes")
    lines = [
        f"{places.airport_label(d['origin'])} -> {places.airport_label(d['dest'])}  |  "
        + ("about " if d.get("taxes_unknown") or d.get("taxes_estimated") else "") + f"{d['cpp']:.2f} cents/pt",
        f"  {d['program']} · {d['cabin'].replace('_', ' ').title()} · {stops}",
        f"  Award: {d['points']:,} points + {taxes}",
        f"  Cash fare: ${d['cash_price']:,.0f} ({d.get('cash_basis') or 'cash fare'})",
        f"  Date: {places.nice_date(d['date'])}"
        + (f" (+{len(d['other_dates'])} more)" if d.get("other_dates") else ""),
    ]
    if d.get("bookable_now"):
        lines.append(f"  Bookable now with the {d['held_miles']:,} miles you already hold")
    return "\n".join(lines)


def _safe_card(d: dict) -> str:
    """One malformed deal must not cost the whole email: the digest is written
    either way, so a dropped card is recoverable and a lost email is not."""
    try:
        return _digest_card(d)
    except Exception:  # noqa: BLE001
        return _row_fallback(d)


def _safe_text(d: dict) -> str:
    try:
        return _digest_text(d)
    except Exception:  # noqa: BLE001
        return f"  {d.get('origin', '?')} -> {d.get('dest', '?')}: couldn't render this deal"


def _row_fallback(d: dict) -> str:
    return (f'<div style="font-family:{_FONT}; font-size:13px; color:#6b7280; padding:10px 0;">'
            f"Couldn't render {_esc(str(d.get('origin', '?')))} &rarr; {_esc(str(d.get('dest', '?')))} "
            f"— see the site for this one.</div>")


def send_digest_email(sections: list[tuple[str, str, list[dict]]], subject: str, intro: str) -> None:
    """sections: (title, subtitle, deals). Empty sections are skipped."""
    sections = [s for s in sections if s[2]]
    if not sections:
        return
    address, app_password = _get_credentials()
    html_body, text = build_digest(sections, intro)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)


def build_digest(sections: list[tuple[str, str, list[dict]]], intro: str) -> tuple[str, str]:
    """(html, plain text) for the digest email."""
    sections = [s for s in sections if s[2]]
    body = ""
    text = [intro, ""]
    for title, subtitle, deals in sections:
        body += f"""
        <div style="font-family:{_FONT}; margin:28px 0 12px;">
          <div style="font-size:19px; font-weight:800; color:#111827;">{_esc(title)}</div>
          <div style="font-size:13px; color:#6b7280; margin-top:2px;">{_esc(subtitle)}</div>
        </div>
        {''.join(_safe_card(d) for d in deals)}"""
        text += [title.upper(), ""] + [_safe_text(d) + "\n" for d in deals]
    html_body = f"""
    <div style="background:#f3f4f6; padding:24px 12px;">
      <div style="max-width:640px; margin:0 auto; font-family:{_FONT};">
        <div style="font-size:24px; font-weight:800; color:#111827;">✈️ Deal Finder</div>
        <div style="font-size:14px; color:#4b5563; line-height:21px; margin-top:6px;">{_esc(intro)}</div>
        {body}
        <div style="font-size:12px; color:#9ca3af; line-height:18px; margin-top:24px;">
          CPP = (cash fare − award taxes) ÷ points. Award space changes fast: confirm on the airline's
          site before transferring points (transfers can't be undone). A deal is a standout when it beats
          what that program's points are normally worth by enough to be worth the transfer: 60% in
          economy, 25% in business and first. Each card names the bar it had to clear.
        </div>
      </div>
    </div>"""
    return html_body, "\n".join(text)
