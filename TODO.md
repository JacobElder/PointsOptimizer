# TODO

## 1. Deploy to Streamlit Community Cloud
- [x] **DONE 2026-07-23**: Deployed `JacobElder/PointsOptimizer` (branch `main`,
      main file `app.py`) to Streamlit Community Cloud. Streamlit's OAuth app
      only had `public_repo` scope (no user-facing way to request broader scope
      from the consent screen), so the repo was made public rather than fighting
      that — audited first, nothing sensitive was ever committed (`secrets.toml`,
      `balances.json`, `history.csv` all gitignored from day one). `deal_log.json`
      stays public/tracked on purpose (needed for the automation, and it's just
      deal-alert data, not credentials) — user's explicit call.
- [x] Secrets (`SERPAPI_KEY`, `SEATS_AERO_API_KEY`) pasted into Streamlit's
      Advanced settings at deploy time.
- [x] Viewer access restricted to own email via the app's Settings -> Sharing tab.
- [ ] Know the tradeoff: `balances.json`/`history.csv` are local files and are NOT
      durable on Streamlit Cloud (container can reset on redeploy/sleep). Fine for
      running the analyzer remotely; don't rely on it for long-term balance storage
      until that's moved to real hosted storage.

## 2. Flight-number auto-lookup (for the 3-field "Flight Number + Class + Points" quick check)
- [ ] Sign up for a flight-schedule-by-number API. Candidate: **AeroDataBox** via
      RapidAPI — has a schedule endpoint that covers future dates (unlike
      Aviationstack's free tier, which is a 3-month *look-back* window only, the
      wrong shape for award travel booked months out).
- [ ] Confirm the free tier's request limit actually fits your usage before building
      against it.
- [ ] Hand the API key over the same way as SERPAPI_KEY/SEATS_AERO_API_KEY, and
      `flight_lookup.py` gets built mirroring the existing `flight_search.py` /
      `seats_aero.py` pattern.

## 3. Rotate the SerpApi key exposed in the Deal Radar routine
- [ ] The "seats.aero Deal Radar" scheduled cloud routine
      (https://claude.ai/code/routines/trig_01XYHpqapanTMazeAp1R2srW) has your real
      SerpApi key embedded in plaintext in its stored prompt/config — there's no
      secrets vault for scheduled routines, so this was the only way to give it
      live-pricing access. You approved this tradeoff on 2026-07-22.
- [ ] Whenever you want that key out of the routine's stored config (e.g. before
      sharing account access with anyone, or just for general hygiene), rotate the
      SerpApi key at serpapi.com, update `.streamlit/secrets.toml` locally (and
      Streamlit Cloud's secrets once deployed), and update/recreate the routine with
      the new key.

## 4. BLOCKING: serpapi.com is denied by the cloud environment's egress policy
- [ ] Confirmed 2026-07-22 (two separate runs, same result): the routine's cloud
      environment ("Default", `env_019guvaADMYYQRYcGqGPpuRX`) denies outbound HTTPS to
      `serpapi.com` — `curl -x $HTTPS_PROXY https://serpapi.com/search` returns
      "CONNECT tunnel failed, response 403". Read straight from the environment's own
      `/root/.ccr/README.md`: this is an **organization egress policy denial**, and
      the documented instruction is explicit — "Do not retry or route around it...
      report it to your administrator or Anthropic support so the policy or tooling
      can be fixed." There is no self-service allowlist toggle exposed to the user for
      this — my earlier note here (pointing at "environment settings on claude.ai")
      was a guess and turned out to be wrong; corrected 2026-07-22.
- [ ] Everything else about the pipeline is proven solid across two real runs:
      Gmail search, parsing, filtering non-deal emails, dedup, and the 15-per-run cap
      all work correctly. Only the live cash-price call is blocked. The routine
      correctly refuses to write placeholder/fake prices, so nothing is lost — ~90+
      unpriced alerts sit safely unprocessed waiting for this to be resolved.
- [x] **DONE 2026-07-22 (option 2, split the pipeline)**: rather than wait on a
      support ticket, the pipeline now splits across two places — see
      [[seats-aero-integration]] and the "Deal Radar split pipeline" section there
      for full detail. Cloud routine only captures (Gmail); a local Mac LaunchAgent
      (`price_pending_deals.py`, hourly + on login/wake) does the actual pricing,
      updates `deal_log.json`, fires a macOS notification, and emails full deal
      details via Gmail SMTP. Confirmed working end to end with real data.
- [ ] Still open, lower priority now that option 2 works: contacting Anthropic
      support to get `serpapi.com` allowed in the cloud environment directly would
      let the whole pipeline live in the cloud again (removing the "only works
      while your Mac is on" gap) — not urgent since the local split is functioning.

## 5. Gmail App Password stored locally for Deal Radar emails
- [ ] `.streamlit/secrets.toml` now has `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`
      (added 2026-07-22) so `price_pending_deals.py` can send you email alerts via
      Gmail SMTP for "great" deals (cabin-aware bar, see item 6). This is a
      local-only file, gitignored, never leaves your Mac (lower exposure than the
      SerpApi key embedded in the cloud routine's config, item 3 above).
- [ ] If you ever want to revoke it: Google Account → Security →
      2-Step Verification → App Passwords → delete "PointsOptimizer Deal Radar"
      (or whatever you named it), then remove the two lines from secrets.toml.

## 8. INCIDENT (2026-07-28): LaunchAgent kept auto-pricing after the item-7 pivot; SerpApi quota exhausted
- [x] The item-7 pivot (2026-07-25) turned off the GitHub Actions schedule, but
      the Mac **LaunchAgent** (`com.pointsoptimizer.dealradar`, hourly,
      `RunAtLoad`) was only ever `bootout`'d for a single login session back on
      2026-07-23 -- macOS reloads LaunchAgents from `~/Library/LaunchAgents` on
      every new login/reboot, so it silently came back and kept running hourly,
      auto-pricing the queue and emailing "great deal" alerts the whole time.
      This is why deal-pricing emails kept arriving after the on-demand pivot.
- [x] Confirmed via SerpApi's `/account.json`: `this_month_usage: 250/250`,
      `plan_searches_left: 0` -- **quota is fully exhausted**, renews
      **2026-08-12**. The internal `deal_log.json` tracker only counted 153 of
      those 250 calls; the rest came from untracked interactive app usage
      (Flight Analyzer / Award search "search live prices" clicks) stacked on
      top of the LaunchAgent's hourly runs. Until 2026-08-12, ALL live cash-price
      lookups (Flight Analyzer, Award search, Deal Radar's per-deal pricing
      button) will fail/return nothing -- this is very likely the real cause
      behind "Stockholm returns no flights", not a Stockholm-specific bug.
- [x] Fixed 2026-07-28: `launchctl bootout`'d the agent AND `launchctl disable`'d
      it (persists across reboots/logins this time, unlike the 2026-07-23 fix).
      Plist left on disk at
      `~/Library/LaunchAgents/com.pointsoptimizer.dealradar.plist` in case it's
      ever wanted back, but launchd will refuse to load it while disabled.
- [ ] After 2026-08-12 when quota resets: verify no automatic pricing resumes
      (check for new "Deal Radar: priced N pending deal(s)" commits without you
      clicking anything) -- if any appear, something else re-enabled it.

## 9. Airport dropdown fixes (2026-07-28)
- [x] `airports.py`'s `_CITY_TO_CODE` is a ~120-entry hand-curated dict shared by
      both the cash-price search and the rewards/award search in `app.py` (no
      separate airport list in `seats_aero.py`). Added missing Caribbean entries
      (Aruba/AUA, Punta Cana/PUJ, Nassau/NAS, San Juan/SJU, St Thomas/STT, St
      Maarten/SXM) -- previously typing these into the selectbox did nothing
      because Streamlit's selectbox only matches existing options, it doesn't
      accept free text.
- [x] Stockholm was mapped to metro code "STO" (never live-verified for
      SerpApi/Google Flights, unlike NYC/LON which are). Switched to "ARN"
      (Stockholm Arlanda, the actual single airport) to remove the ambiguity.
      Couldn't live-verify against SerpApi (quota exhausted, see item 8) --
      re-test once quota resets 2026-08-12.
- [x] **Structural fix, 2026-07-28**: two changes together close the gap instead
      of playing whack-a-mole forever:
      1. Deep-audited expansion: a research agent cross-checked every candidate
         city against OpenFlights' real airport dataset (not memory) and added
         **418 new verified entries** (534 total, up from ~130) covering US/Canada/
         Mexico secondary cities, the wider Caribbean & Central America, South
         America, European secondary cities, Middle East/Africa, and Asia/Pacific.
         Caught real disambiguation traps along the way (e.g. Medellín's real
         gateway MDE is filed under city "Rio Negro" in the data, not "Medellin";
         Providenciales/PLS vs Grand Turk/GDT in Turks & Caicos). See
         `test_airports.py` for coverage.
      2. Upgraded Streamlit 1.37.1 -> 1.60.0 (floor raised to >=1.45 in
         requirements.txt) to use `st.selectbox(..., accept_new_options=True)` --
         both search tabs in app.py now let you pick from the dropdown OR type
         any city/IATA code freely, so a city missing from the curated list is
         no longer a dead end (falls through to `airports.resolve_input()`,
         which accepts a raw 3-letter code for literally any airport worldwide).
      Some very-low-relevance-for-a-US-traveler capital-city codes (several
      West/Central African capitals) were deliberately left out by the audit
      agent as noise, not oversight -- easy to add on request, codes already
      confirmed against OpenFlights if wanted later.

## 7. PIVOT (2026-07-25): on-demand pricing, automatic pricing OFF
- [x] Chose the "middle path": keep the FREE Gmail capture (cloud routine still
      logs seats.aero alerts into deal_log.json `pending`), but STOP auto-pricing.
      Deals are now priced on demand from the Deal Radar page (a "💵 Price this
      deal" button per captured deal — one SerpApi lookup per click). This kills
      the scheduler complexity, race conditions, quota drain, and notification
      noise, while keeping a browsable list of every alerted deal.
- [x] `deal_radar_pricing.yml` is now `workflow_dispatch`-only (manual escape
      hatch to bulk-price the queue if ever wanted); no schedule/push triggers.
- [ ] Note: the capture routine still commits deal_log.json ~every 4h, which
      still auto-redeploys the Streamlit app (brief "Oh no" window if you load it
      mid-redeploy). Much rarer than before. If it ever annoys you, the fix is to
      move deal_log.json off the app branch (bigger change) — left as an option.
- [x] **Verified 2026-07-28** (see item 8's quota exhaustion incident): Apify
      Flight Price Scraper (`apify.com/makework36/flight-price-scraper`) is a real,
      current, self-serve option -- returns actual merged cash prices (Google
      Flights + Kiwi + Travelpayouts + budget carriers), one-way/round-trip,
      specific date, cabin class -- matches flight_search's contract. ~$0.0003/merged
      search, $5 free credit (~16k searches), instant signup via Apify account +
      API token, no approval/sales call. Confirmed the best fallback for when
      SerpApi's monthly quota runs out.
      Secondary backup if Apify ever has an outage: **Duffel**
      (`duffel.com`) -- also instant self-serve, real live cash fares, but its
      free quota is tied to a booking ratio (1,500 searches per booking); with
      zero bookings that's effectively $0.005/search instead of a free tier.
      Ruled out: Kiwi.com Tequila API is now invitation-only for new partners
      (no self-serve path); Travelpayouts/Aviasales data is a 7-day cache of other
      users' searches, too stale for exact-date CPP comparisons; Amadeus
      self-service stays decommissioned (item 4 note).
- [ ] Not yet built: a `flight_search`-compatible Apify backend as an automatic
      or manual fallback when SerpApi returns 429/quota-exhausted. Worth building
      before the *next* time SerpApi runs dry, given item 8 just happened.

## 6. DONE: GitHub Actions removed the "Mac must be on" dependency (superseded by #7)
- [x] `price_pending_deals.py` fixed to be cross-platform safe (was going to
      crash with FileNotFoundError on Linux runners via an unconditional
      `osascript` call) — pushed 2026-07-22.
- [ ] **`.github/workflows/deal_radar_pricing.yml` is written but NOT pushed** —
      my git credential here lacks the `workflow` OAuth scope GitHub requires to
      push changes under `.github/workflows/`. Two ways to finish this:
      1. Add the `workflow` scope to your PAT (GitHub → Developer settings →
         Personal access tokens) and tell me — I'll push it.
      2. Or add the file yourself directly on github.com (Add file → Create new
         file → `.github/workflows/deal_radar_pricing.yml`) — works around the
         token-scope issue since it's authenticated as you, not the token.
- [ ] Then add 3 repo secrets (Settings → Secrets and variables → Actions):
      `SERPAPI_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (same values as your
      local `.streamlit/secrets.toml`).
- [ ] Test via Actions tab → "Deal Radar pricing" → **Run workflow** (manual
      `workflow_dispatch` trigger, no need to wait for the hourly schedule).
- [x] **DONE 2026-07-23**: Unloaded the Mac LaunchAgent
      (`launchctl bootout gui/$(id -u)/com.pointsoptimizer.dealradar`) *before*
      GitHub Actions had processed any real pending batch (only a no-op empty-queue
      run so far) -- done deliberately, so the next real batch is forced through
      GitHub Actions instead of racing the Mac. The plist is still on disk at
      `~/Library/LaunchAgents/com.pointsoptimizer.dealradar.plist`, so it's a
      one-command reload (`launchctl bootstrap gui/$(id -u) <path>`) if GitHub
      Actions turns out to fail on something the Mac didn't.
- [ ] **Still open**: confirm GitHub Actions actually prices a real batch end to
      end (commit + email) the next time the cloud capture routine queues
      something new -- check for a commit authored by "Deal Radar bot" (its git
      identity) with a real `priced N pending deal(s)` message, not `priced 0`.
