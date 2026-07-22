# TODO

## 1. Deploy to Streamlit Community Cloud
- [ ] Go to share.streamlit.io, sign in with GitHub, authorize `JacobElder/PointsOptimizer`.
- [ ] New app -> branch `main`, main file `app.py`.
- [ ] Settings -> Secrets -> paste:
  ```
  SERPAPI_KEY = "..."
  SEATS_AERO_API_KEY = "..."
  ```
- [ ] Settings -> restrict viewer access to your own email (app is public by default).
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
      Gmail SMTP for "great" deals (≥2.0¢/pt). This is a local-only file,
      gitignored, never leaves your Mac (lower exposure than the SerpApi key
      embedded in the cloud routine's config, item 3 above).
- [ ] If you ever want to revoke it: Google Account → Security →
      2-Step Verification → App Passwords → delete "PointsOptimizer Deal Radar"
      (or whatever you named it), then remove the two lines from secrets.toml.
