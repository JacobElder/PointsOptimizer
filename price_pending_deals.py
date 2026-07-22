"""
Prices whatever the cloud routine queued into deal_log.json's "pending" list,
since that routine's cloud environment cannot reach serpapi.com (org egress
policy block, see TODO.md item 4) but this can.

Runs from two places: a Mac LaunchAgent (hourly, whenever the Mac is on) and
a GitHub Actions scheduled workflow (hourly, always-on, no Mac dependency --
see .github/workflows/deal_radar_pricing.yml). Both write to the same shared
deal_log.json via git, so this pulls before reading and handles a rejected
push gracefully in case both fire close together.

Safe to run by hand any time too: `python3 price_pending_deals.py`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timezone

import check_alerts
import deal_email
import deal_log
import flight_search

CAP_PER_RUN = 15  # protects the shared SerpApi 250/month free quota


def _notify_mac(message: str) -> None:
    if not shutil.which("osascript"):
        return  # not on macOS (e.g. running in GitHub Actions) -- email is the notification there
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{escaped}" with title "Deal Radar"'
    subprocess.run(["osascript", "-e", script], check=False)


def main() -> None:
    if not flight_search.is_configured():
        print("SERPAPI_KEY not configured (env var or .streamlit/secrets.toml) -- nothing to do.")
        sys.exit(1)

    pull = subprocess.run(["git", "pull", "--ff-only", "origin", "main"], capture_output=True, text=True)
    if pull.returncode != 0:
        print(f"git pull failed, aborting so we don't work on a stale local copy:\n{pull.stderr}")
        sys.exit(1)

    data = deal_log.load()
    pending = data["pending"]
    if not pending:
        print("No pending deals to price.")
        return

    pending_sorted = sorted(pending, key=lambda d: d["points"])
    to_price, still_pending = pending_sorted[:CAP_PER_RUN], pending_sorted[CAP_PER_RUN:]

    priced = check_alerts.evaluate_alerts(to_price)
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    great = []
    for p in priced:
        p["key"] = deal_log.make_key(p["program"], p["origin"], p["dest"], p["cabin"], p["date"], p["points"])
        p["checked_at"] = checked_at
        p["notified"] = deal_log.is_great(p)
        if p["notified"]:
            great.append(p)
        data["deals"].append(p)

    data["pending"] = still_pending
    deal_log.save(data)

    subprocess.run(["git", "add", "deal_log.json"], check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", f"Deal Radar: priced {len(priced)} pending deal(s)"],
    )
    if commit.returncode == 0:
        push = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
        if push.returncode != 0:
            print(f"git push failed (someone else pushed in the meantime?):\n{push.stderr}")

    print(f"Priced {len(priced)} deal(s), {len(still_pending)} left in queue, {len(great)} great.")
    if great:
        best = max(great, key=lambda d: d["cpp"])
        msg = (
            f"{len(great)} great deal(s)! Best: {best['origin']}->{best['dest']} "
            f"{best['program']} {best['cabin'].title()} {best['cpp']:.2f}c/pt"
        )
        _notify_mac(msg)

        if deal_email.is_configured():
            try:
                deal_email.send_deal_alert_email(great)
                print(f"Sent email with {len(great)} great deal(s).")
            except Exception as e:
                print(f"Email send failed: {e}")
        else:
            print("Gmail not configured (GMAIL_ADDRESS/GMAIL_APP_PASSWORD) -- skipped email.")


if __name__ == "__main__":
    main()
