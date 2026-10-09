# PointsOptimizer — notes for Claude

Personal award-travel tool. A daily GitHub Actions run (`deal_finder.py`) scans seats.aero,
prices awards against Google Flights cash fares, picks standout deals and emails a digest;
`deal_digest.json` is committed and shown on the Streamlit site. `README.md` explains how
everything works — read it before changing scoring. `docs/decisions.md` records why the
valuation and ranking rules are what they are — add an entry when you change one. `TODO.md`
lists open items.

## The daily routine: run → audit → fix → repeat

The user asks "check the digest" / "did it run today? audit and review" most days. Do this:

1. **Did it run?** `gh run list --repo JacobElder/PointsOptimizer --workflow "Deal Finder" -L 6`
   then `git pull --ff-only`. Runs are started in layers (see "How the run is started");
   later layers stop at "Today's digest already exists" — that is correct, not a failure.
   If nothing ran by ~16:30 UTC, find which layer failed before starting one by hand.
2. **Read the run log's key lines** (replace RUN_ID):
   ```
   gh run view RUN_ID --repo JacobElder/PointsOptimizer --log | cut -f3 \
     | sed -E 's/^[0-9T:.-]+Z //' | grep -E "^Scan:|^Pricing|Selection|settle|warning|^Round-trip|^Fresh re-pricing|^Switched|^Return legs|retries|failed for"
   ```
   Healthy: no `::warning`; `Selection: … settled in ≤12 round(s)` everywhere; flight
   lookups ~50–80 total; round-trip checks mostly priced (>90%); `no_fare` single digits;
   scan `failed`/`aborted` empty. Compare with the previous 2–3 days, not absolutes.
3. **Audit the digest:** `make audit` (offline: card math, bars, unverified, estimated round
   trips) then `make audit-live` (re-prices emailed cards against today's Google fares; free,
   ~2 min). For a past day: `.venv/bin/python tools/audit_digest.py <git-rev> [--live]`.
   Repeat emails: `tools/audit_digest.py --repeats`.
4. **Fix what's wrong** — see "How to fix" — then push, and tell the user plainly what ran,
   what you checked, what was wrong, what you changed, and what to watch tomorrow.

A deal falling below its bar a day later at live fares is usually the fare moving, not a bug —
say so, but look for a pattern (same route every day, borderline margins).

## How to fix (lessons from this project)

- **Write a failing test that reproduces the bug first, and confirm it fails without the fix.**
  Two fixes for selection churn (0ec75fb, 7c7b74f) were guesses that didn't work; the third
  (d10106b) came with a reproducing test and did. A test whose bound is computed from the
  constant you're changing proves nothing — use a literal.
- Measure on real data before and after (the digest history in git is the dataset:
  `git log -- deal_digest.json`). Replay a rule over past digests before shipping it.
- `make test` (pytest via `.venv`; warnings are errors). Never use the base Anaconda python
  (protobuf clash with fast-flights).
- Commit messages explain the why with the numbers that found it (see `git log`). End with
  the Co-Authored-By / Claude-Session lines the harness gives you. Push to `main` when tests
  pass; the user has asked for changes to be pushed.
- Don't run `make find` / a full scan to test: one scan is ~240 of the 1,000 seats.aero calls a
  day shared with the user's own searches, and it re-emails. Use the digest and the fakes in
  `test_run_end_to_end.py`. `make audit-live` uses only Google (free) — fine to run.

## How the run is started (GitHub's scheduler drops runs since late Aug 2026)

1. Cron slots 08:17 and 12:17 UTC (`.github/workflows/deal_finder.yml`) — usually fire hours
   late, sometimes not at all.
2. cron-job.org (the user's account) calling `workflow_dispatch` with `backup=true` daily at
   10:00 America/New_York — set up 2026-10-06, test returned 204 and started a run. Shows up as
   event `workflow_dispatch`. It uses a fine-grained token (Actions read/write, this repo only); if
   these runs stop appearing, the token probably expired — the user gets cron-job.org failure emails.
   **As of Oct 8 it has never fired on schedule:** no `workflow_dispatch` run on Oct 7 or Oct 8 and
   no failure email (only the account-activation email, Oct 7 01:43 UTC). Since even a skipping run
   would show up, the job itself isn't executing — the user is checking activation / enabled /
   schedule on cron-job.org. Until it's confirmed, treat this layer as missing.
3. Claude routine `trig_01EkoS6ACSSFEpBo7wY5qCUq` at 16:00 UTC: pushes a line to
   `.github/run-request`, which starts the workflow via a push trigger. Inspect with the
   RemoteTrigger tool (`list_runs`, `get_run_log`). It has no connectors on purpose — if you
   recreate it, clear them (create attaches all of the user's connectors by default).

Every layer except a plain manual run skips if today's `deal_digest.json` exists. "Today" is the
**UTC** date: anything started after 8 PM Eastern counts as the next day's run (a cron-job.org
test at 9:53 PM ET on Oct 6 produced Oct 7's batch that night; the next day's layers then skipped).

A run **cancelled** with no steps executed is GitHub, not the code: check the job's annotations
(`gh api repos/JacobElder/PointsOptimizer/check-runs/<job-id>/annotations`) for "The job was not
acquired by Runner" and githubstatus.com for an Actions incident. It only matters if no other
layer produced that day's digest. Workflows are pinned to `ubuntu-24.04` (see TODO.md).

## Things that are easy to get wrong

- seats.aero `direct` means "one flight number", not nonstop, and isn't reliable even then
  (Ethiopian EWR–JNB, two numbers and two stops, is flagged direct). Stops come from the
  flight details (`award_trips`). American reports 0 seats meaning "unknown".
- seats.aero taxes are in cents. Gulf carriers quote taxes in AED/QAR/SAR (FX fallback).
- A deal is valued at the lower of the one-way fare and half a round trip. When no round
  trip can be priced it's valued at 81% of the one-way (`RT_HALF_ESTIMATE_RATIO`) — never at
  the full one-way.
- Within a card, the leading date/program is chosen by `choice_value` (points you'd actually
  spend via `funding.opportunity_cost_usd`, quality discounts, 15% nonstop preference,
  exploration capped per card). Between cards, ranking uses `rank_value`.
- Ranking uses `rank_cash` (today's fare capped at 1.25× the route's typical fare); the card's
  CPP and the bar use the real fare. Don't "fix" the mismatch — it's deliberate.
- Email repeats are suppressed per route/program/cabin (`reported_routes`) unless ≥15% more
  CPP or ≤90% of the points.
- Cash fares: fast-flights (free Google Flights) is the source. SerpApi is a 5-call fallback
  that almost never runs; the user may remove it. Google sometimes throttles the Actions
  runner (empty results) — the round-trip retry and estimate exist for that.
- Saved fares are reused for up to 14 days; reuse counts dip when an old batch ages out.
- `transfer_bonuses.json`: set `checked` whenever you check bonuses, even if nothing changed.
- Public repo: never commit balances or secrets. Wording: say "per-run lookup limit", never
  "budget", for Google lookups (the user reads budget as money).

## Talking to the user

Plain language, lead with the answer: did it run, are the deals right, what you fixed.
Name deals worth a look with route, program, points and live ¢/pt. Be honest about your own
earlier mistakes. Remind them of open items only when relevant: GIST_TOKEN, deleting the old
paused "seats.aero Deal Radar" routine at claude.ai/code/routines, the SerpApi decision.

## Where things stand (update this section as it changes)

- **Goal:** call the system finished after 5 consecutive clean runs — email arrives with no one
  stepping in, every emailed deal verified and still above its bar at live fares, no repeat
  emails, no code fixes needed — then switch to a weekly `make audit-live`.
- **Clean days so far:** Oct 6, Oct 7, Oct 8 (3 of 5). Fixes from Oct 5 (repeat suppression, fare-spike cap,
  Emirates cabin links, test isolation of balance files, runner pin) had their first real run Oct 6
  and worked. Oct 7's digest came from the hand-clicked cron-job.org test (01:53 UTC), so the
  unattended 10:00 ET dispatch has not yet produced a digest on its own (see layer 2 above). Oct 8
  came from GitHub's own schedule (15:36 UTC); 7 emailed, all above bar live, one intended repeat
  (EWR–MAD Alaska, 70k → 55k points). Flight-detail lookups 91 (Oct 7) and 93 (Oct 8), above the
  ~50–80 norm and far under the 260 cap, tracking more round-trip checks (~790 vs 562) — fine
  unless it keeps climbing. Borderline margins on Oct 8: JFK–BGI 2.76¢ vs 2.71 bar, JFK–SJU 2.12¢
  vs 2.08 (the parked "small margin above the bar" idea).
- **Oct 9: not clean by the letter, no code fault.** GitHub schedule (15:18 UTC), healthy log (74
  flight lookups, 507/510 round trips priced, all selections ≤7 rounds), 5 emailed, 0 repeats, 0
  offline problems. JFK–NRT Flying Blue 115k+$699 (Oct 27) fell below its bar at live fares
  6 h later: $3,540 → $2,949, 2.47¢ → 1.96¢ vs 2.27 bar. The email fare was 1.22× the route's
  typical $2,895 (the fare model's estimate, which was right) and the margin was only 9%. Replay
  of Oct 6–9: 3 emailed cards would have been below the bar at the typical fare (JFK–CPT Oct 6,
  EWR–MUC Oct 7, JFK–NRT Oct 9); only NRT actually fell. 1 of 3 isn't enough to ship a rule. If
  another "fare well above typical + thin margin" card falls, that's the data for the parked
  margin idea. Whether this resets the streak is the user's call.
- **One-off local searches** (e.g. the Oct 7 SE Asia search via `award_scanner.scan` +
  `price_promising`) write live quotes into `cash_quotes.json`; `git checkout -- cash_quotes.json`
  before `git pull` or the pull aborts.
- **Local balances** (`balances.json`, gitignored, Mac only) were wiped by a test from Sep 26 to
  Oct 5 and restored Oct 6 from the user: Chase 139,915 · Bilt 103,000 · Wells Fargo 0. Airline
  miles are in `program_balances.json` and the `PROGRAM_BALANCES` secret.
- **Ideas parked until the data asks for them:** a small margin above the bar for borderline deals;
  more fresh Google fares per run (reuse fell when the Sep 16–17 batch aged out); a mild preference
  for fewer total points. See `docs/decisions.md` for decisions already made.
