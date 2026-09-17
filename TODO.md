# TODO

Open items only. Resolved history (Streamlit deploy, egress-blocked cloud
pricing, the July SerpApi quota incident, airport dropdown fixes) lives in git
history before 2026-09-16.

## Needs you

- [ ] The paused "seats.aero Deal Radar" routine at claude.ai/code/routines can be
      deleted: the alert-email pipeline it fed was removed from the repo 2026-09-16.
- [ ] Optional: delete `~/Library/LaunchAgents/com.pointsoptimizer.dealradar.plist`.
      It's disabled and runs a script that no longer exists.
- [ ] Optional hygiene: rotate the SerpApi key (no longer stored in the routine).

## Possible improvements

- [ ] One place to update balances (e.g. a private Gist read by both the site and
      the daily run) instead of Wallet + the PROGRAM_BALANCES secret.
- [ ] Flight Search as one flow: search → ranked results → funding shown inline.
- [ ] Flight-number quick check (needs a schedule API such as AeroDataBox).
- [ ] If Google changes its results page, `cash_price_check.yml` (weekly) fails
      and GitHub emails you; meanwhile the Deal Finder falls back to SerpApi
      (capped at 5 calls/run) and prices fewer deals.
