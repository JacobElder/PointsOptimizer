# TODO

Open items only. Resolved history (Streamlit deploy, egress-blocked cloud
pricing, the July SerpApi quota incident, airport dropdown fixes) lives in git
history before 2026-09-16.

## Needs you

- [ ] If you ever turn seats.aero alert emails back on, re-enable the paused
      "seats.aero Deal Radar" routine at claude.ai/code/routines (paused
      2026-09-16; the Deal Finder replaced it).
- [ ] Optional: delete `~/Library/LaunchAgents/com.pointsoptimizer.dealradar.plist`.
      It's disabled, points at the base Anaconda Python (which can't import
      fast-flights), and is superseded by GitHub Actions.
- [ ] Optional hygiene: rotate the SerpApi key (no longer stored in the routine).

## Possible improvements

- [ ] Durable storage for balances/history on Streamlit Cloud (currently reset on
      container restart).
- [ ] Flight-number quick check (needs a schedule API such as AeroDataBox).
- [ ] If Google changes its results page, `cash_price_check.yml` (weekly) fails
      and GitHub emails you; meanwhile the Deal Finder falls back to SerpApi
      (capped at 5 calls/run) and prices fewer deals.
