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
MAX_PRICE_ATTEMPTS = 4  # give up re-pricing a deal after this many transient failures

# Fields evaluate_alerts() adds on top of a pending entry; stripped when we put a
# transiently-failed deal back on the queue so it retains its original shape.
_PRICING_FIELDS = (
    "taxes_usd", "cash_price", "cpp", "verdict", "priced_ok", "error",
    "price_error", "key", "checked_at", "notified",
)


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
        # A non-fast-forward (diverged/dirty local checkout, e.g. from an earlier
        # rejected push) must not wedge the pipeline forever -- hard-reset to the
        # server-owned state and carry on. deal_log.json is shared server state,
        # so discarding local divergence is the correct recovery.
        print(f"git pull not fast-forward; resetting to origin/main:\n{pull.stderr}")
        subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True)
        reset = subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, text=True)
        if reset.returncode != 0:
            print(f"git reset --hard failed, aborting:\n{reset.stderr}")
            sys.exit(1)

    data = deal_log.load()
    pending = data["pending"]
    if not pending:
        print("No pending deals to price.")
        return

    # FIX 4: quarantine malformed entries so one bad row can't crash the batch.
    # Invalid entries stay on the queue (harmless -- skipped before any API call)
    # rather than being silently dropped.
    valid = [e for e in pending if deal_log.is_valid_pending(e)]
    invalid = [e for e in pending if not deal_log.is_valid_pending(e)]
    if invalid:
        print(f"Skipping {len(invalid)} malformed pending entry(ies).")

    valid_sorted = sorted(valid, key=lambda d: int(d["points"]))
    to_price, overflow = valid_sorted[:CAP_PER_RUN], valid_sorted[CAP_PER_RUN:]

    priced = check_alerts.evaluate_alerts(to_price)
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    great, newly_priced, retry = [], [], []
    for p in priced:
        if not p.get("priced_ok", True):
            # FIX 3: transient lookup failure -- keep on the queue for a bounded
            # number of retries instead of permanently recording NO CASH PRICE.
            attempts = int(p.get("price_attempts", 0)) + 1
            if attempts < MAX_PRICE_ATTEMPTS:
                entry = {k: v for k, v in p.items() if k not in _PRICING_FIELDS}
                entry["price_attempts"] = attempts
                retry.append(entry)
                continue
            # else: fall through and record it as a definitive NO CASH PRICE.
        p["key"] = deal_log.make_key(p["program"], p["origin"], p["dest"], p["cabin"], p["date"], p["points"])
        p["checked_at"] = checked_at
        p["notified"] = deal_log.is_great(p)
        if p["notified"]:
            great.append(p)
        newly_priced.append(p)
        data["deals"].append(p)

    data["pending"] = invalid + overflow + retry
    deal_log.save(data)

    subprocess.run(["git", "add", "deal_log.json"], check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", f"Deal Radar: priced {len(newly_priced)} pending deal(s)"],
    )
    pushed = False
    if commit.returncode == 0:
        push = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
        if push.returncode == 0:
            pushed = True
        else:
            # Another runner pushed first: our commit didn't land, so those deals
            # never reached origin. Reset to avoid wedging, and DO NOT email --
            # the winning runner priced the same batch and notifies for it.
            print(f"git push rejected; resetting to origin/main (winner will notify):\n{push.stderr}")
            subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True)
            subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, text=True)

    print(f"Priced {len(newly_priced)} deal(s), {len(data['pending'])} left in queue, {len(great)} great.")

    # FIX 2: only notify/email when our commit actually landed on origin.
    if great and not pushed:
        print(f"{len(great)} great deal(s) found but the commit didn't land -- skipping email to avoid a phantom/duplicate alert.")
    elif great and pushed:
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
