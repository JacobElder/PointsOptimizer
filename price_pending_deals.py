"""
Prices the deals the capture routine queued into deal_log.json's "pending" list.

Cash prices come from cash_quotes (free fast-flights first, SerpApi fallback,
quotes persisted and reused across nearby dates), so a bulk run is cheap.

Runs by hand (`python3 price_pending_deals.py`) or from the manual GitHub
Actions workflow. Flags:
    --no-git     price and save locally only; no pull/commit/push
    --limit N    max deals to price this run (default CAP_PER_RUN)
    --no-email   skip the macOS notification and Gmail alert
    --serpapi-cap N  max paid SerpApi fallback calls (default SERPAPI_CAP_PER_RUN)

Git safety: this never runs `git reset --hard`. It commits only the two data
files, refuses to run off `main`, and on any git failure stops and reports
instead of discarding anything.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import cash_quotes
import check_alerts
import deal_email
import deal_log
import flight_search

CAP_PER_RUN = 60
SERPAPI_CAP_PER_RUN = 10
MAX_PRICE_ATTEMPTS = 4  # stop auto-retrying a deal after this many transient failures
DATA_FILES = ["deal_log.json", "cash_quotes.json"]

# Premium cabins first (more points at stake), then fewest points within a tier.
_CABIN_PRIORITY = {"FIRST": 0, "BUSINESS": 1, "PREMIUM_ECONOMY": 2, "ECONOMY": 3}

# Fields evaluate_alerts() adds; stripped when a deal stays on the queue.
_PRICING_FIELDS = (
    "taxes_usd", "cash_price", "cpp", "verdict", "priced_ok", "error", "price_error", "key",
    "checked_at", "notified", "price_status", "nonstop_cash_price", "cash_provider",
    "cash_is_approx", "cash_quote_date",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _notify_mac(message: str) -> None:
    if not shutil.which("osascript"):
        return  # not on macOS (e.g. GitHub Actions) -- email is the notification there
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{escaped}" with title "Deal Radar"'
    subprocess.run(["osascript", "-e", script], check=False)


def _sync_from_origin() -> bool:
    """Bring local main up to date with origin. Never discards local work."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        print(f"On branch '{branch}', not main -- refusing to commit pipeline data here. Use --no-git.")
        return False
    if _git("status", "--porcelain", "--", *DATA_FILES).stdout.strip():
        print(f"Uncommitted edits to {DATA_FILES} -- commit or discard them first.")
        return False
    pull = _git("pull", "--rebase", "--autostash", "origin", "main")
    if pull.returncode != 0:
        _git("rebase", "--abort")
        print(f"git pull failed; nothing priced, nothing discarded:\n{pull.stderr}")
        return False
    return True


def _commit_and_push(message: str) -> bool:
    if _git("add", "--", *DATA_FILES).returncode != 0:
        print("git add failed")
        return False
    commit = _git("commit", "-m", message, "--", *DATA_FILES)
    if commit.returncode != 0:
        print(f"git commit failed:\n{commit.stdout}{commit.stderr}")
        return False
    for attempt in range(3):
        push = _git("push", "origin", "main")
        if push.returncode == 0:
            return True
        # Most likely the capture routine pushed meanwhile: replay our commit on top.
        pull = _git("pull", "--rebase", "--autostash", "origin", "main")
        if pull.returncode != 0:
            _git("rebase", "--abort")
            print(f"push rejected and rebase failed; commit kept locally for next run:\n{pull.stderr}")
            return False
    print("push still rejected after retries; commit kept locally for next run.")
    return False


def _requeue_unpriced_deals(data: dict, today: str) -> int:
    """Older runs filed failed lookups (quota 429s, timeouts, too-far-out dates)
    into `deals` as NO CASH PRICE, which blocked them forever. Put future-dated
    ones back on the queue. Idempotent: current runs never file unpriced deals."""
    keep, moved = [], 0
    for d in data["deals"]:
        if d.get("cpp") is None and str(d.get("date", "")) >= today:
            data["pending"].append({k: v for k, v in d.items() if k not in _PRICING_FIELDS})
            moved += 1
        else:
            keep.append(d)
    data["deals"] = keep
    return moved


def _expire_past_deals(data: dict, today: str) -> int:
    live, expired = [], []
    for p in data["pending"]:
        (expired if isinstance(p, dict) and str(p.get("date", "9999")) < today else live).append(p)
    data["pending"] = live
    data.setdefault("expired", []).extend(expired)
    return len(expired)


def price_queue(data: dict, limit: int) -> tuple[list[dict], dict]:
    """Price up to `limit` queued deals in place. Returns (newly priced, status counts)."""
    today_d = datetime.now(timezone.utc).date()
    candidates = []
    for p in data["pending"]:
        if not deal_log.is_valid_pending(p) or int(p.get("price_attempts", 0)) >= MAX_PRICE_ATTEMPTS:
            continue
        days_out = (datetime.strptime(p["date"], "%Y-%m-%d").date() - today_d).days
        if 0 <= days_out <= cash_quotes.MAX_LOOKAHEAD_DAYS:
            candidates.append(p)
    candidates.sort(key=lambda d: (_CABIN_PRIORITY.get(str(d["cabin"]).upper(), 4), int(d["points"])))
    to_price = candidates[:limit]
    if not to_price:
        return [], {}

    results = check_alerts.evaluate_alerts(to_price)
    by_id = {id(orig): res for orig, res in zip(to_price, _align(to_price, results))}
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_pending, newly_priced, counts = [], [], {}
    for p in data["pending"]:
        res = by_id.get(id(p))
        if res is None:
            new_pending.append(p)
            continue
        status = res.get("price_status", "priced" if res.get("cpp") is not None else "failed")
        counts[status] = counts.get(status, 0) + 1
        if status == "priced":
            res["key"] = deal_log.make_key(p["program"], p["origin"], p["dest"], p["cabin"], p["date"], p["points"])
            res["checked_at"] = checked_at
            res["notified"] = False
            res.pop("price_attempts", None)
            newly_priced.append(res)
            continue
        entry = dict(p)
        entry["last_price_status"] = status
        entry["last_checked_at"] = checked_at
        if status == "failed":
            entry["price_attempts"] = int(p.get("price_attempts", 0)) + 1
        new_pending.append(entry)

    data["pending"] = new_pending
    data["deals"].extend(newly_priced)
    return newly_priced, counts


def _align(to_price: list[dict], results: list[dict]) -> list[dict]:
    """evaluate_alerts sorts its output by CPP; map results back to input order."""
    remaining = list(results)
    ordered = []
    for p in to_price:
        for i, r in enumerate(remaining):
            if all(r.get(k) == p.get(k) for k in ("origin", "dest", "date", "cabin", "program", "points")):
                ordered.append(remaining.pop(i))
                break
        else:
            ordered.append({**p, "price_status": "failed", "cpp": None})
    return ordered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--limit", type=int, default=CAP_PER_RUN)
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--serpapi-cap", type=int, default=SERPAPI_CAP_PER_RUN,
                    help="max paid SerpApi fallback calls this run (fast-flights is unlimited)")
    args = ap.parse_args(argv)
    flight_search.SERPAPI_MAX_CALLS = args.serpapi_cap

    if not flight_search.is_configured():
        print("No cash-price provider available (install fast-flights or set SERPAPI_KEY).")
        return 1
    if not args.no_git and not _sync_from_origin():
        return 1

    data = deal_log.load()
    data.pop("serpapi_usage", None)  # replaced by SerpApi's own account.json; local count drifted
    today = datetime.now(timezone.utc).date().isoformat()
    requeued = _requeue_unpriced_deals(data, today)
    expired = _expire_past_deals(data, today)

    newly_priced, counts = price_queue(data, args.limit)
    great = [d for d in newly_priced if deal_log.is_great(d)]

    if great and not args.no_email:
        best = max(great, key=lambda d: d["cpp"])
        _notify_mac(f"{len(great)} great deal(s)! Best: {best['origin']}->{best['dest']} "
                    f"{best['program']} {best['cabin'].title()} {best['cpp']:.2f}c/pt")
        if deal_email.is_configured():
            try:
                deal_email.send_deal_alert_email(great)
                for d in great:
                    d["notified"] = True
                print(f"Sent email with {len(great)} great deal(s).")
            except Exception as e:
                print(f"Email send failed (deals stay notified=False): {e}")

    print(f"Requeued {requeued} previously-unpriced deal(s); expired {expired} past-dated.")
    print(f"Priced {len(newly_priced)} ({len(great)} great); statuses {counts}; {len(data['pending'])} left in queue.")
    remaining = flight_search.serpapi_account_remaining() if flight_search.serpapi_configured() else None
    if remaining is not None:
        print(f"SerpApi searches left this month (per serpapi.com): {remaining}")

    if not (requeued or expired or counts):
        print("Nothing changed; no save/commit.")
        return 0
    deal_log.save(data)
    cash_quotes.save(cash_quotes.load())  # prune past-dated quotes; ensures the file exists for git add
    if args.no_git:
        return 0
    return 0 if _commit_and_push(f"Deal Radar: priced {len(newly_priced)} pending deal(s) [skip ci]") else 1


if __name__ == "__main__":
    sys.exit(main())
