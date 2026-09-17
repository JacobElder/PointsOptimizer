# TODO

Open items only. Resolved history (Streamlit deploy, egress-blocked cloud
pricing, the July SerpApi quota incident, airport dropdown fixes) lives in git
history before 2026-09-16.

## Needs you

- [ ] **Delete the paused "seats.aero Deal Radar" routine** at claude.ai/code/routines
      (the tool here can pause but not delete). Its pipeline was removed 2026-09-16.
- [ ] Optional hygiene: rotate the SerpApi key (no longer stored in the routine).

## Possible improvements

- [ ] **Add GIST_TOKEN** (classic token, `gist` scope only) to secrets.toml,
      Streamlit Cloud secrets and GitHub secrets, then delete the PROGRAM_BALANCES
      secret. See README → "Balances".
- [ ] Flight-number quick check (needs a schedule API such as AeroDataBox).
- [ ] If Google changes its results page, `cash_price_check.yml` (weekly) fails
      and GitHub emails you; meanwhile the Deal Finder falls back to SerpApi
      (capped at 5 calls/run) and prices fewer deals.
