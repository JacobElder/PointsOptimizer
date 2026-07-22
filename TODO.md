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
