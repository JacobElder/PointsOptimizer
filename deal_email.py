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
    return "\n".join(lines)


def _deal_card_html(d: dict) -> str:
    flight = html_lib.escape(str(d.get("flight_number") or "—"))
    program = html_lib.escape(d["program"])
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
              </td>
            </tr>
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


def _build_html(deals: list[dict], best: dict) -> str:
    economy = [d for d in deals if d["cabin"].upper() not in _PREMIUM_CABINS]
    premium = [d for d in deals if d["cabin"].upper() in _PREMIUM_CABINS]

    return f"""
    <div style="font-family: -apple-system, Helvetica, Arial, sans-serif; max-width:600px; margin:0 auto;">
      <h2 style="color:#1a202c; margin-bottom:4px;">seats.aero Deal Radar</h2>
      <p style="color:#4a5568; margin-top:0;">
        {len(deals)} deal(s) cleared the "great" bar. Best: <strong>{best['cpp']:.2f}&cent;/pt</strong>
        on {best['origin']} &rarr; {best['dest']} ({best['program']}).
      </p>
      {_section_html("✈️ Economy / Premium Economy", economy)}
      {_section_html("\U0001F6CB️ Business / First", premium)}
    </div>
    """


def send_deal_alert_email(deals: list[dict]) -> None:
    """Sends one email covering all the given (already-priced, "great") deals."""
    if not deals:
        return
    address, app_password = _get_credentials()

    best = max(deals, key=lambda d: d["cpp"])
    subject = (
        f"seats.aero Deal Radar: {len(deals)} great deal(s), best {best['cpp']:.2f}c/pt "
        f"({best['origin']}->{best['dest']})"
    )
    plain_body = "\n\n".join(_format_deal(d) for d in sorted(deals, key=lambda d: -d["cpp"]))
    html_body = _build_html(deals, best)

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
