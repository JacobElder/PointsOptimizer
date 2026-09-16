# TODO

Open items only. Resolved history (Streamlit deploy, egress-blocked cloud
pricing, the July SerpApi quota incident, airport dropdown fixes) lives in git
history before 2026-09-16.

## Needs you

- [ ] **Add the `SEATS_AERO_API_KEY` repo secret** (GitHub → Settings → Secrets
      and variables → Actions) so the daily Deal Finder workflow runs.
      `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` and `SERPAPI_KEY` are already set.
      Then Actions → Deal Finder → Run workflow once to confirm.
- [ ] **Decide on the Gmail capture routine** ("seats.aero Deal Radar" on
      claude.ai, every 4 hours). seats.aero alerts are off, so it finds nothing,
      but each run still uses your Claude plan's usage. Pausing it costs nothing.
- [ ] **Fill in the watchlist** in `scan_config.json` (destinations, dates, cabins).
- [ ] **Confirm Bilt transfers work without an open card** in the Bilt app. If
      not, set `transfers_without_card=False` for the `bilt` pool in
      `cards_data.py` (the scan then drops Alaska, Emirates, Etihad, Qatar, Turkish).
- [ ] **Enter your Bilt balance** on the Wallet page.
- [ ] **Re-verify Virgin Atlantic sweet spots** in `award_charts.py` on
      virginatlantic.com: ANA First may now be 72.5k (not 60k), and Delta One to
      Europe is tiered with ~$1,000 surcharges.
- [ ] **Confirm unresolved transfer ratios** listed in `cards_data.py`'s header
      (Capital One TAP/JetBlue, Citi Emirates/Wyndham) on the issuers' sites.
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
