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


def _format_deal(d: dict) -> str:
    flight = d.get("flight_number") or "unknown flight #"
    lines = [
        f"{d['origin']} -> {d['dest']}  |  {d['program']}  |  {d['cabin'].title()}",
        f"  Flight: {flight}",
        f"  Travel date: {d['date']}",
        f"  Points: {d['points']:,}",
        f"  Taxes/fees: {d['taxes']:.2f} {d.get('currency', 'USD')}",
        f"  Cash price (same route/date/cabin): ${d['cash_price']:,.2f}",
        f"  >>> {d['cpp']:.2f} cents per point <<<",
    ]
    return "\n".join(lines)


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
    body = "\n\n".join(_format_deal(d) for d in sorted(deals, key=lambda d: -d["cpp"]))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(body)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)
