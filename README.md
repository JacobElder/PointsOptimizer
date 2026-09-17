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
make find-resend  # same, but email every current deal (to test the email)
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
| **🏆 Top Deals** (home) | The latest daily scan: book-now deals with miles you already hold, your watchlist, and the top 20. **Scan now** refreshes on demand (quick ~4 min or full ~9 min, no email). |
| **✈️ Flight Search** | One flow: pick a route and **one date**, a **date range**, **any time**, or **outbound + return** (two one-way awards) → ranked deals by cents per point against the live cash fare, each showing exactly how to pay (miles you already hold first, then the best card transfer). Tools: check a deal by hand, look up cash fares. |
| **💳 Wallet** | Card point balances and miles already in airline programs; transfer partners per pool. |
| **🗺️ Card Roadmap** | For each card you might get: which partners it unlocks, and a live preview of the deals on your routes you can't book today. |

`app.py` is only the navigation; each page lives in `views/`.

---

## Deal Finder — how deals are found

`deal_finder.py` runs daily on GitHub Actions (`.github/workflows/deal_finder.yml`, 7am ET) or on demand with `make find`:

1. **Scan** (`award_scanner.py`) — seats.aero Cached Search across `scan_config.json` routes (plus watchlist destinations), one query per program per cabin, in programs your **active** point pools can transfer to **plus** programs you already hold miles in. ~145,000 awards for ~200 of the 1,000 daily API calls.
2. **Estimate** (`fare_model.py`) — predicted cash fare with uncertainty for every award (regression on distance, cabin, region; per-route corrections as real fares accumulate), giving the probability it clears the cabin's great-deal bar.
3. **Price** (`cash_quotes.py`) — real Google Flights fares for the most promising candidates, highest expected value first. Quotes are saved in `cash_quotes.json` and reused for dates within ±7 days on the same route and cabin.
4. **Round-trip check** — for deals near the top, also price a 7-night round trip. The award is valued against the **lower** of the one-way fare and half the round trip (one-way fares are often far above half a round trip: EWR–CPT business $5,084 one-way vs $2,519 per direction).
5. **Rank** — confirmed deals by dollars saved above the bar: `(cash − taxes) − points × bar`. One entry per destination + cabin; other dates, origins and programs listed under it; 5 slots reserved for economy.
6. **Book now with miles you already hold** — awards fully covered by miles already sitting in a program (e.g. JetBlue, American), listed first. Programs you can't top up from a card (American, Delta) only show awards your balance fully covers.
7. **Watchlist** — destinations you care about are always reported when they clear their bar, even outside the top 20 (see below).
8. **Report** — `deal_digest.json` (shown on Deal Radar) and an email of deals not reported in the last 14 days, watchlist hits first.

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

  `origins` and `cabins` narrow the match, `start`/`end` bound travel dates, and `bar` sets the CPP to report at (omit it for the usual 1.5¢/2.0¢). Watchlist destinations are added to the scan automatically and share a separate lookup budget round-robin (so one entry with thousands of matches can't crowd out the rest). Some destinations have no seats.aero coverage from NYC in any program (as of 2026-09-16: Oaxaca, Tbilisi/Kutaisi); those entries report nothing until coverage appears.
- **Balances (card points and miles already in airline programs):** edit on the Wallet page. To keep **one copy everywhere** (Mac app, hosted site, daily email), create a GitHub **classic** token with only the `gist` scope and add it as `GIST_TOKEN` in `.streamlit/secrets.toml`, in the Streamlit Cloud app's secrets, and as a GitHub repo secret. Balances then live in a secret Gist (`pointsoptimizer_balances.json`), created automatically from your local balances. Without it, balances are local files and the daily run uses the `PROGRAM_BALANCES` secret. Southwest isn't covered by seats.aero (its points have a roughly fixed value).
- **Programs:** follow your point pools automatically (Chase UR, Wells Fargo, and Bilt, whose balance can be transferred without an open card). Getting a new card (e.g. Capital One Venture X): change its `status` from `"planned"` to `"held"` in `cards_data.py`; its pool becomes active and its airline partners that seats.aero covers are scanned from the next run. `python deal_finder.py --include-planned` previews that without changing anything.

---

## Accuracy notes

- **CPP** = `(cash fare − award taxes in USD) / points × 100`. Taxes are converted with a live ECB rate (frankfurter.app), static fallback offline. seats.aero API taxes are in cents.
- **Cash fares** are for one adult and not necessarily on the award's airline (see matching rules above). Deals near the top are valued at the lower of the one-way fare and half a 7-night round trip; deals further down and "other dates" use the one-way fare, so their CPP can read high.
- **Award data** is seats.aero Cached Search (updated every few days, not live). Results older than 10 days are dropped. Always confirm on the airline's site before transferring points — transfers are irreversible.
- **Coverage:** seats.aero covers Aeroplan, Flying Blue, BA, Iberia, JetBlue, Singapore, United, Virgin Atlantic, Qatar, Turkish, Etihad, Qantas, Finnair, Aeromexico and more, but not LifeMiles, Asia Miles, TAP, EVA, Aer Lingus or hotel programs.
- **Transfer partners** in `cards_data.py` were last verified 2026-09-16; known source conflicts are listed in its header.
- **Streamlit Cloud storage isn't durable:** `balances.json` / `program_balances.json` reset when the hosted app restarts.
- **The repo is public:** `deal_digest.json`, `cash_quotes.json` and `deal_log.json` (award and fare data only, no credentials or balances) are visible. `deal_log.json` is the retired alert pipeline's history, kept because its recorded fares train the fare model.

---

## Development

```bash
make test                       # full suite; network is mocked
.venv/bin/python cash_price_check.py   # confirm free Google Flights lookups work here
```

CI: `tests.yml` on every push; `cash_price_check.yml` weekly and whenever the price provider changes.

### Module map

| Module | Responsibility |
|--------|----------------|
| `deal_finder.py` | Daily scan → estimate → price → match → round-trip check → rank → digest/email; card previews |
| `route_search.py` | Best-CPP search on one route across a date range, and outbound + return pairs |
| `award_scanner.py` | seats.aero Cached Search across routes and your programs |
| `fare_model.py` | Cash-fare estimates with uncertainty |
| `cash_quotes.py` | Saved fare quotes, nearby-date reuse, comparable-fare matching |
| `flight_search.py` | Google Flights fares (one-way and round-trip): fast-flights, capped SerpApi fallback |
| `valuation.py` | CPP math, currency conversion, cabin-aware verdict |
| `funding.py` | How to pay for an award: held miles first, then the best transfer |
| `seats_aero.py` | Single-date award search for Flight Search; program name mapping |
| `cards_data.py` | Cards, point pools, transfer partners |
| `ledger.py` | Card balances and miles held in programs (secret Gist when GIST_TOKEN is set, else local files) |
| `places.py` | City, country and airline names for codes (`airport_coords.json`, `airline_names.json`) |
| `digest_view.py` | Deal cards on the site |
| `deal_email.py` | Digest email (Gmail SMTP) |
| `airports.py` | City/airport dropdown data |
