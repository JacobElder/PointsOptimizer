"""
Sends a detailed email for Deal Radar's "great" deals, via Gmail SMTP with an
App Password (not OAuth) -- simplest option for a local, unattended script.
No Gmail "send" API is available to Claude Code sessions (only draft
creation), so this has to happen from the local script itself.

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

_PREMIUM_CABINS = {"BUSINESS", "FIRST"}


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


def _format_deal(d: dict) -> str:
    """Plain-text rendering, used as the fallback part of the email."""
    flight = d.get("flight_number") or "unknown flight #"
    lines = [
        f"{d['origin']} -> {d['dest']}  |  {d['program']}  |  {d['cabin'].title()}  "
        f"|  {d['cpp']:.2f} cents/pt",
        f"  Flight: {flight}",
        f"  Travel date: {d['date']}",
        f"  Points: {d['points']:,}",
        f"  Taxes/fees: {d['taxes']:.2f} {d.get('currency', 'USD')}",
        f"  Cash price (same route/date/cabin): ${d['cash_price']:,.2f}",
    ]
    if d.get("note"):
        lines.append(f"  {d['note']}")
    if d.get("listing_url"):
        lines.append(f"  View listing: {d['listing_url']}")
    return "\n".join(lines)


def _deal_card_html(d: dict) -> str:
    flight = html_lib.escape(str(d.get("flight_number") or "—"))
    program = html_lib.escape(d["program"])
    link_html = ""
    if d.get("listing_url"):
        url = html_lib.escape(d["listing_url"])
        link_html = (
            f'<a href="{url}" style="color:#2563eb; text-decoration:none; font-size:13px;">'
            f"View on seats.aero &rarr;</a>"
        )
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #e2e8f0; border-radius:8px; margin-bottom:12px;">
      <tr>
        <td style="padding:14px 16px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="font-size:16px; font-weight:600; color:#1a202c;">
                {d['origin']} &rarr; {d['dest']} &middot; {program} &middot; {d['cabin'].title()}
              </td>
              <td align="right" style="font-size:22px; font-weight:700; color:#15803d; white-space:nowrap;">
                {d['cpp']:.2f}&cent;/pt
              </td>
            </tr>
          </table>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
            <tr>
              <td style="font-size:13px; color:#4a5568; line-height:20px;">
                {d['points']:,} pts + ${d['taxes']:.2f} {d.get('currency', 'USD')} taxes
                &nbsp;vs.&nbsp; ${d['cash_price']:,.2f} cash<br>
                Travel {d['date']} &middot; Flight {flight}
                {f"<br>{html_lib.escape(d['note'])}" if d.get("note") else ""}
              </td>
            </tr>
            {f'<tr><td style="padding-top:6px;">{link_html}</td></tr>' if link_html else ""}
          </table>
        </td>
      </tr>
    </table>
    """


def _section_html(title: str, deals: list[dict]) -> str:
    if not deals:
        return ""
    cards = "".join(_deal_card_html(d) for d in sorted(deals, key=lambda d: -d["cpp"]))
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px;">
      <tr>
        <td style="font-size:14px; font-weight:700; color:#1a202c; text-transform:uppercase;
                   letter-spacing:0.04em; padding-bottom:10px; border-bottom:2px solid #1a202c;
                   margin-bottom:12px;">
          {title} ({len(deals)})
        </td>
      </tr>
    </table>
    {cards}
    """


def _build_html(deals: list[dict], best: dict, intro: str | None = None) -> str:
    economy = [d for d in deals if d["cabin"].upper() not in _PREMIUM_CABINS]
    premium = [d for d in deals if d["cabin"].upper() in _PREMIUM_CABINS]

    return f"""
    <div style="font-family: -apple-system, Helvetica, Arial, sans-serif; max-width:600px; margin:0 auto;">
      <h2 style="color:#1a202c; margin-bottom:4px;">seats.aero Deal Radar</h2>
      <p style="color:#4a5568; margin-top:0;">
        {html_lib.escape(intro) if intro else f'{len(deals)} deal(s) cleared the "great" bar.'}
        Best: <strong>{best['cpp']:.2f}&cent;/pt</strong>
        on {best['origin']} &rarr; {best['dest']} ({html_lib.escape(best['program'])}).
      </p>
      {_section_html("✈️ Economy / Premium Economy", economy)}
      {_section_html("\U0001F6CB️ Business / First", premium)}
    </div>
    """


def send_deal_alert_email(deals: list[dict], subject: str | None = None, intro: str | None = None) -> None:
    """Sends one email covering all the given (already-priced, "great") deals."""
    if not deals:
        return
    address, app_password = _get_credentials()

    best = max(deals, key=lambda d: d["cpp"])
    subject = subject or (
        f"seats.aero Deal Radar: {len(deals)} great deal(s), best {best['cpp']:.2f}c/pt "
        f"({best['origin']}->{best['dest']})"
    )
    plain_body = "\n\n".join(_format_deal(d) for d in sorted(deals, key=lambda d: -d["cpp"]))
    html_body = _build_html(deals, best, intro)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)


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
        chips += _chip("Seen before", "#f3f4f6", "#4b5563")

    cabin = d["cabin"].replace("_", " ").title()
    stops = "Nonstop" if d.get("direct") else "Connecting"
    bar = d.get("watch_bar", d["great_floor"])
    surplus = d.get("watch_surplus_usd", d["surplus_usd"])

    rows = _row("Award", f"<b>{d['points']:,} points</b> + ${d['taxes_usd']:,.0f} taxes &amp; fees")
    fare = f"<b>${d['cash_price']:,.0f}</b> <span style='color:#6b7280'>· {_esc(d.get('cash_basis') or 'cash fare')}</span>"
    extras = []
    if d.get("round_trip_half") is not None and d.get("one_way_cash") and d["cash_price"] < d["one_way_cash"]:
        extras.append(f"one-way ${d['one_way_cash']:,.0f}")
    if d.get("same_carrier_cash"):
        extras.append(f"{_esc(d['program'].split()[0])}'s own fare ${d['same_carrier_cash']:,.0f}")
    if d["cabin"] == "FIRST":
        extras.append("first class compared with the business fare")
    if d.get("cash_is_approx"):
        extras.append("fare from a date within 7 days")
    if extras:
        fare += f"<br><span style='font-size:12px; color:#6b7280'>{' · '.join(extras)}</span>"
    rows += _row("Cash fare", fare)
    rows += _row("Value", f"<span style='color:#15803d; font-weight:600'>${surplus:,.0f} more</span> than the "
                          f"{bar:.1f}¢/pt bar")
    dates = f"<b>{places.nice_date(d['date'])}</b>"
    others = d.get("other_dates") or []
    if others:
        shown = ", ".join(places.nice_date(x, weekday=False) for x in others[:6])
        more = f" and {len(others) - 6} more" if len(others) > 6 else ""
        dates += f"<br><span style='font-size:12px; color:#6b7280'>Also: {shown}{more}</span>"
    rows += _row("Dates", dates)
    if d.get("airlines"):
        rows += _row("Airlines", _esc(places.airline_names(d["airlines"])))
    if d.get("alternatives"):
        rows += _row("Also", "<br>".join(_esc(a) for a in d["alternatives"]))

    link = _google_flights_url(d["origin"], d["dest"], d["date"])
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
          <a href="{link}" style="font-size:13px; color:#2563eb; text-decoration:none; font-weight:600;">
            Check the cash fare on Google Flights &rarr;</a>
        </div>
      </td></tr>
    </table>"""


def _digest_text(d: dict) -> str:
    import places
    lines = [
        f"{places.airport_label(d['origin'])} -> {places.airport_label(d['dest'])}  |  {d['cpp']:.2f} cents/pt",
        f"  {d['program']} · {d['cabin'].replace('_', ' ').title()} · {'Nonstop' if d.get('direct') else 'Connecting'}",
        f"  Award: {d['points']:,} points + ${d['taxes_usd']:,.0f} taxes",
        f"  Cash fare: ${d['cash_price']:,.0f} ({d.get('cash_basis') or 'cash fare'})",
        f"  Date: {places.nice_date(d['date'])}"
        + (f" (+{len(d['other_dates'])} more)" if d.get("other_dates") else ""),
    ]
    if d.get("bookable_now"):
        lines.append(f"  Bookable now with the {d['held_miles']:,} miles you already hold")
    return "\n".join(lines)


def send_digest_email(sections: list[tuple[str, str, list[dict]]], subject: str, intro: str) -> None:
    """sections: (title, subtitle, deals). Empty sections are skipped."""
    sections = [s for s in sections if s[2]]
    if not sections:
        return
    address, app_password = _get_credentials()
    body = ""
    text = [intro, ""]
    for title, subtitle, deals in sections:
        body += f"""
        <div style="font-family:{_FONT}; margin:28px 0 12px;">
          <div style="font-size:19px; font-weight:800; color:#111827;">{_esc(title)}</div>
          <div style="font-size:13px; color:#6b7280; margin-top:2px;">{_esc(subtitle)}</div>
        </div>
        {''.join(_digest_card(d) for d in deals)}"""
        text += [title.upper(), ""] + [_digest_text(d) + "\n" for d in deals]
    html_body = f"""
    <div style="background:#f3f4f6; padding:24px 12px;">
      <div style="max-width:640px; margin:0 auto; font-family:{_FONT};">
        <div style="font-size:24px; font-weight:800; color:#111827;">✈️ Deal Finder</div>
        <div style="font-size:14px; color:#4b5563; line-height:21px; margin-top:6px;">{_esc(intro)}</div>
        {body}
        <div style="font-size:12px; color:#9ca3af; line-height:18px; margin-top:24px;">
          CPP = (cash fare − award taxes) ÷ points. Award space changes fast: confirm on the airline's
          site before transferring points (transfers can't be undone). Bars: 1.5¢ economy, 2.0¢ business/first.
        </div>
      </div>
    </div>"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content("\n".join(text))
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)
