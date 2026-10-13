# TODO

Open items only. Resolved history (Streamlit deploy, egress-blocked cloud
pricing, the July SerpApi quota incident, airport dropdown fixes) lives in git
history before 2026-09-16.

## Needs you

- [ ] **Delete the paused "seats.aero Deal Radar" routine** at claude.ai/code/routines
      (the tool here can pause but not delete). Its pipeline was removed 2026-09-16.
- [ ] SerpApi was removed 2026-10-12 (code, GitHub repo secret and local secrets.toml line).
      Left: delete `SERPAPI_KEY` from the Streamlit Cloud app's secrets; optionally close
      the serpapi.com account.

## Possible improvements

- [ ] **Move workflows to Ubuntu 26 deliberately** (pinned to `ubuntu-24.04` on 2026-10-05 because
      `ubuntu-latest` switches from Oct 19). Change one workflow, run it by hand, compare the digest.

- [ ] Once `award_history.json` has a few weeks of data, consider raising
      `HISTORY_WEIGHT` (0.3) so route-relative cheapness counts for more than deal size.
- [ ] Premium economy was just added to the scan but `fare_model` has no premium-economy
      training data yet; its estimates there lean on economy x1.8 until quotes accumulate.

- [ ] **Re-check `program_values.json`** a few times a year (what each program's
      points are worth). Last verified 2026-09-23 from upgradedpoints.com.

- [ ] **Keep `transfer_bonuses.json` current.** Last checked 2026-10-11 (Chase→Marriott 70% to Oct 15;
      nothing for Bilt or Wells Fargo; Citi→JAL 30% skipped: JAL isn't a Citi partner in cards_data.py). Update its `checked` date whenever you look, even if
      nothing changed; the email nags once it's 21 days old. Now automated: a weekly Claude routine (Mondays) opens a
      pull request with the update — merge it to keep the list current.
- [ ] Later: move the watchlist (`scan_config.json`) into the private balances Gist so
      travel plans aren't visible in the public repo.
- [ ] Maybe later: tune the email after a week of real use (fewer/more watchlist deals,
      bars).

- [ ] Optional, later (user: "if I care to bother"): **add GIST_TOKEN** for one shared copy
      of balances. Without it the daily email's pay line doesn't know card balances and uses
      the Sep 16 PROGRAM_BALANCES miles. Order matters: add it to local secrets.toml and open
      the Wallet page FIRST (seeds the Gist from local files), then Streamlit Cloud secrets,
      then the GitHub secret; delete PROGRAM_BALANCES after a clean run. See README → "Balances".
- [ ] Flight-number quick check (needs a schedule API such as AeroDataBox).
- [ ] If Google changes its results page, `cash_price_check.yml` (weekly) fails
      and GitHub emails you; meanwhile the Deal Finder can't price new fares and
      leans on saved quotes (up to 14 days old).
