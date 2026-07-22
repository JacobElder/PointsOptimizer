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
