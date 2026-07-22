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

## 4. BLOCKING: allowlist serpapi.com for the Deal Radar routine's cloud environment
- [ ] Confirmed 2026-07-22: the routine's cloud environment ("Default",
      `env_019guvaADMYYQRYcGqGPpuRX`) blocks outbound HTTPS to `serpapi.com` at the
      network/proxy level (403 on CONNECT, per the environment's own proxy status
      endpoint) — this is a network egress policy, NOT SerpApi's 250/month quota.
- [ ] Everything else works: the routine successfully searched Gmail, fetched and
      parsed 101 alert emails across 32 threads, filtered out 11 non-deal
      confirmation emails, deduped to 90 valid unpriced candidates, and correctly
      capped to the cheapest 15 per run before hitting this wall. No further code
      changes should be needed once this is fixed.
- [ ] Fix: go to the environment settings for "Default" on claude.ai and allowlist
      `serpapi.com` (or reroute `flight_search.py`'s cash-price lookups through a
      provider that's already allowlisted there). I don't have a tool that can
      change this myself — it's account/infra config outside the RemoteTrigger API.
- [ ] Once fixed, either wait for the next scheduled fire (every 4 hours) or trigger
      a manual "Run now" from the routine page — it'll pick up all 90 pending
      candidates automatically since none were marked processed.
