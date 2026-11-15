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

- [ ] **cron-job.org backup layer has never fired on schedule** (set up 2026-10-06; only the
      hand-clicked test ran). Fix it on cron-job.org (activation / enabled / schedule / token) or
      delete the job; the GitHub schedule and the Claude routine cover the run meanwhile.
- [ ] Minor, your call: the public digest's held-miles section reveals a lower bound on the miles
      held in each airline program (it says a deal is payable from miles already held). Keep, or
      drop that detail from the committed digest.

## Possible improvements

- [ ] **Move workflows to Ubuntu 26 deliberately** (pinned to `ubuntu-24.04` on 2026-10-05 because
      `ubuntu-latest` switches from Oct 19). Change one workflow, run it by hand, compare the digest.

- [ ] Consider raising `HISTORY_WEIGHT` (0.3) so route-relative cheapness counts for more than
      deal size. Now actionable: `award_history.json` spans Sep 23 onward (~7 weeks).

- [ ] **Re-check `program_values.json`** a few times a year (what each program's
      points are worth). Last verified 2026-09-23 from upgradedpoints.com.

- [ ] **Keep `transfer_bonuses.json` current.** Last checked 2026-11-09 (PR #5, merged Nov 15). Update its `checked` date whenever you look, even if
      nothing changed; the email nags once it's 21 days old. Now automated: a weekly Claude routine (Mondays) opens a
      pull request with the update — merge it to keep the list current.
- [ ] Later: move the watchlist (`scan_config.json`) into the private balances Gist so
      travel plans aren't visible in the public repo.

- [ ] Optional, later (user: "if I care to bother"): **add GIST_TOKEN** for one shared copy
      of balances. Without it the daily email's pay line doesn't know card balances and uses
      the Sep 16 PROGRAM_BALANCES miles. Order matters: add it to local secrets.toml and open
      the Wallet page FIRST (seeds the Gist from local files), then Streamlit Cloud secrets,
      then the GitHub secret; delete PROGRAM_BALANCES after a clean run. See README → "Balances".
- [ ] Flight-number quick check (needs a schedule API such as AeroDataBox).
- [ ] If Google changes its results page, `cash_price_check.yml` (weekly) fails
      and GitHub emails you; meanwhile the Deal Finder can't price new fares and
      leans on saved quotes (up to 14 days old).
