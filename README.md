# PointsOptimizer ✈️

A personal toolkit for deciding **when to redeem credit-card points and when to hold them**, and for finding the genuinely great award deals in a flood of seats.aero availability.

The core question: *an award in front of you is worth some cents-per-point (CPP) today — is that good enough to book, or are you better off holding the points?*

---

## Quick start

```bash
cd ~/Documents/GitHub/PointsOptimizer
make setup     # creates .venv with everything (once)
make app       # run the Streamlit app
make find      # scan seats.aero, price the best awards, email new standouts
make test
```

Use the project `.venv` (the Makefile does). `fast-flights` needs protobuf ≥ 5.27, which clashes with packages in a base Anaconda environment.

The app is also deployed on Streamlit Community Cloud (redeploys on every push to `main`; viewer access restricted to the owner).

### Configuration

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (gitignored):

| Key | Used for | Cost |
|-----|----------|------|
| `SEATS_AERO_API_KEY` | Award availability (Cached Search) | seats.aero Pro, $9.99/mo, includes 1,000 API calls/day |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Emailing deal digests (Gmail SMTP) | Free (Google Account → Security → App Passwords) |
| `SERPAPI_KEY` *(optional)* | Fallback cash prices if free lookups fail | Free tier, 250 searches/month; capped per run |

**Cash prices need no key.** They come from Google Flights via `fast-flights` (free, no quota). SerpApi is the same Google Flights data behind a paid API, kept only as a fallback.

For the scheduled Deal Finder, add the same keys as GitHub repo secrets (Settings → Secrets and variables → Actions).

---

## Pages

| Page | What it does |
|------|--------------|
| **Home** (`app.py`) — Flight Analyzer | Search award space (seats.aero) and cash fares for a route, click **Use this award** to get CPP against a comparable cash fare, see which of your point pools can fund it, and run the redeem-vs-hold simulation. |
| **1 · Wallet** | Point balances per pool, transfer partners, active vs. locked pools (`balances.json`, local). |
| **3 · History** | Past analyses (`history.csv`) and calibration of simulation parameters. |
| **4 · Roadmap** | What-if for cards you might open: which transfer partners each would unlock. |
| **5 · Deal Radar** | The Deal Finder digest (ranked standouts), plus older Gmail-captured alerts with on-demand pricing. |
| **6 · Redeem or Hoard** | Standalone simulation calculator. |

---

## Deal Finder — how deals are found

`deal_finder.py` runs daily on GitHub Actions (`.github/workflows/deal_finder.yml`, 7am ET) or on demand with `make find`:

1. **Scan** (`award_scanner.py`) — seats.aero Cached Search across `scan_config.json` routes, only in programs your **active** point pools can transfer to. ~16,000 awards for ~21 API calls.
2. **Estimate** (`fare_model.py`) — predicted cash fare with uncertainty for every award (regression on distance, cabin, region; per-route corrections as real fares accumulate), giving the probability it clears the cabin's great-deal bar.
3. **Price** (`cash_quotes.py`) — real Google Flights fares for the most promising candidates, highest expected value first. Quotes are saved in `cash_quotes.json` and reused for dates within ±7 days on the same route and cabin.
4. **Round-trip check** — for deals near the top, also price a 7-night round trip. The award is valued against the **lower** of the one-way fare and half the round trip (one-way fares are often far above half a round trip: EWR–CPT business $5,084 one-way vs $2,519 per direction).
5. **Rank** — confirmed deals by dollars saved above the bar: `(cash − taxes) − points × bar`. One entry per destination + cabin; other dates, origins and programs listed under it; 5 slots reserved for economy.
6. **Watchlist** — destinations you care about are always reported when they clear their bar, even outside the top 20 (see below).
7. **Report** — `deal_digest.json` (shown on Deal Radar) and an email of deals not reported in the last 14 days, watchlist hits first.

### Which cash fare an award is compared against

- **Nonstop award** → cheapest fare with **at most one stop**. Not the cheapest nonstop: one-way nonstop fares on legacy carriers are often several times the one-stop fare (JFK–ZRH Swiss business $8,919 vs $1,468 with one stop) and would produce fake 10¢+ "deals".
- **Connecting award** → cheapest fare, any stops.
- **First class** → valued against the **business** fare. Google's "first" results are business or mixed-cabin on most routes.
- The nonstop fare and the award airline's own fare are shown for context, never used for CPP.

### Great-deal bar (cabin-aware)

| Cabin | Book at or above | Skip below |
|-------|------------------|------------|
| Economy / Premium Economy | 1.5¢ | 1.0¢ |
| Business / First | 2.0¢ | 1.0¢ |

Defined once in `deal_log.py` (`verdict_for`) and used everywhere: Flight Analyzer, Deal Radar, emails.

### Changing what gets scanned

- **Routes, cabins, date window:** edit `scan_config.json`.
- **Watchlist:** add entries to `watchlist` in `scan_config.json`. Only `dests` is required:

  ```json
  "watchlist": [
    {"label": "Japan cherry blossoms", "dests": ["NRT", "HND"], "cabins": ["BUSINESS"],
     "start": "2027-03-20", "end": "2027-04-10", "bar": 1.8},
    {"label": "Lisbon, any time", "dests": ["LIS"]}
  ]
  ```

  `origins` and `cabins` narrow the match, `start`/`end` bound travel dates, and `bar` sets the CPP to report at (omit it for the usual 1.5¢/2.0¢). Watchlist destinations are added to the scan automatically and get priority for price lookups.
- **Programs:** follow your point pools automatically (Chase UR, Wells Fargo, and Bilt, whose balance can be transferred without an open card). Getting a new card (e.g. Capital One Venture X): change its `status` from `"planned"` to `"held"` in `cards_data.py`; its pool becomes active and its airline partners that seats.aero covers are scanned from the next run. `python deal_finder.py --include-planned` previews that without changing anything.

---

## Legacy: Gmail-captured alerts

Before the Deal Finder, a claude.ai routine captured seats.aero alert **emails** into `deal_log.json`, priced by `price_pending_deals.py` (manual GitHub workflow or `make price`). seats.aero alerts are now turned off, so nothing new arrives; the captured history stays browsable on Deal Radar. `price_pending_deals.py` never runs `git reset`, commits only its data files, and refuses to run off `main`.

---

## Accuracy notes

- **CPP** = `(cash fare − award taxes in USD) / points × 100`. Taxes are converted with a live ECB rate (frankfurter.app), static fallback offline. seats.aero API taxes are in cents.
- **Cash fares** are one-way, one adult, and not necessarily on the award's airline (see matching rules above). One-way fares can be high compared with half a round trip, so treat CPP as an upper-bound signal and check the round trip before transferring points.
- **Award data** is seats.aero Cached Search (updated every few days, not live). Results older than 10 days are dropped. Always confirm on the airline's site before transferring points — transfers are irreversible.
- **Coverage:** seats.aero covers Aeroplan, Flying Blue, BA, Iberia, JetBlue, Singapore, United, Virgin Atlantic, Qatar, Turkish, Etihad, Qantas, Finnair, Aeromexico and more, but not LifeMiles, Asia Miles, TAP, EVA, Aer Lingus or hotel programs.
- **Transfer partners** in `cards_data.py` were last verified 2026-09-16; known source conflicts are listed in its header.
- **Streamlit Cloud storage isn't durable:** `balances.json` / `history.csv` reset when the container restarts.
- **The repo is public:** `deal_log.json`, `deal_digest.json` and `cash_quotes.json` (award and fare data only, no credentials) are visible.

---

## Development

```bash
make test                       # full suite; network is mocked
.venv/bin/python cash_price_check.py   # confirm free Google Flights lookups work here
```

CI: `tests.yml` on every push; `cash_price_check.yml` whenever the price provider changes.

### Module map

| Module | Responsibility |
|--------|----------------|
| `deal_finder.py` | Daily scan → estimate → price → rank → digest/email |
| `award_scanner.py` | seats.aero Cached Search across configured routes and your transferable programs |
| `fare_model.py` | Cash-fare estimates with uncertainty (`airport_coords.json` from OpenFlights) |
| `cash_quotes.py` | Saved fare quotes, nearby-date reuse, comparable-fare matching |
| `flight_search.py` | Google Flights cash fares: fast-flights (free) with capped SerpApi fallback |
| `seats_aero.py` | Single-route award search for the Flight Analyzer |
| `simulation.py` | Monte Carlo redeem-vs-hold valuation |
| `cards_data.py` | Cards, point pools, transfer partners |
| `deal_log.py` | Captured-alert state and the cabin-aware verdict |
| `check_alerts.py` | CPP + verdict for captured alerts; FX rates |
| `price_pending_deals.py` | Prices captured alerts (manual) |
| `seats_aero_alerts.py` | Parses seats.aero alert emails (legacy capture) |
| `deal_email.py` | HTML deal emails via Gmail SMTP |
| `return_finder.py` | Return-leg search and round-trip valuation |
| `ledger.py` | Local balances and history |
| `airports.py` | City/airport dropdown data |
| `award_charts.py` | Curated sweet-spot reference |
| `going_parse.py` | Paste-to-parse for Going deal emails |
