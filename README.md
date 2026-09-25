# PointsOptimizer ✈️

A personal toolkit for deciding **when to redeem credit-card points and when to hold them**, and for finding the genuinely great award deals in a flood of seats.aero availability.

The core question: *an award in front of you is worth some cents-per-point (CPP) today — is that good enough to book, or are you better off holding the points?*

---

## Quick start

```bash
cd ~/Documents/GitHub/PointsOptimizer
make setup     # creates .venv with everything (once)
make app       # run the Streamlit app
make find         # scan seats.aero, price the best awards, email new standouts
make find-resend  # same, but email every current deal (to test the email)
make find-quiet   # same, no email
make check        # confirm free Google Flights lookups work from this machine
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

`deal_finder.py` runs daily on GitHub Actions (`.github/workflows/deal_finder.yml`) or on demand with `make find`. The cron asks for 08:00 UTC, but GitHub's shared scheduler runs hours late, so the email usually lands mid-morning US Eastern — it's a daily digest, not a fixed time.

1. **Scan** (`award_scanner.py`) — seats.aero Cached Search across `scan_config.json` routes (plus watchlist destinations), one query per program per cabin, in programs your **active** point pools can transfer to **plus** programs you already hold miles in. ~145,000 awards for ~200 of the 1,000 daily API calls.
2. **Estimate** (`fare_model.py`) — predicted cash fare with uncertainty for every award, giving the probability it clears that program's standout bar.
3. **Price** (`cash_quotes.py`) — real Google Flights fares for the most promising candidates, highest expected value first. Quotes are saved in `cash_quotes.json` and reused for nearby dates on the same route and cabin — within 2 days for trips under 45 days out, 7 under 120, 14 beyond, because fares move much more near departure. A quarter of the lookup budget is spent on route/cabin pairs with no recent quote, so the estimates keep learning where they're wrong rather than only confirming what they already like.
4. **Round-trip check** — near-top deals also price a round trip (7 nights, falling back to 4 or 2 so trips near the edge of the booking window still get checked). The award is valued against the **lower** of the one-way fare and half the round trip, because one-way fares run far above half a round trip. A deal that still can't be round-trip priced is flagged and ranked down.
4b. **Re-price the shortlist** — every deal that will actually be reported is re-priced on its own date with no borrowed quote. Reported deals are selected precisely because their fare came in high: a hand-check of 9 found all 9 cheaper, one by 68%.
5. **Verify** (`award_trips.py`, 1 call per reported deal) — the actual flights, times, connections, cabin of each leg, seats left, and a direct booking link. Deals are **dropped** if the award is gone, has repriced above what the scan saw, or shows 0 seats in a program that reports seat counts. Deals are **ranked lower** for a mixed cabin (a "business" award with an economy leg), an airport change mid-trip, a very long itinerary, or award data older than 5 days — each flagged on the card.
6. **Book now with miles you already hold** — awards fully covered by miles already in a program (e.g. JetBlue, American), listed first. Programs you can't top up from a card only show awards your balance fully covers.
7. **Watchlist** — destinations you care about are always reported when they clear their bar, even outside the top 20.
8. **Self-check** — every fresh fare is compared with what `fare_model` predicted; the run logs the median and 90th-percentile error and warns in the GitHub log if the median passes `ESTIMATE_DRIFT_WARN_PCT` (45%). Shown on Top Deals.
9. **Report** — `deal_digest.json` (shown on Top Deals) and an email of deals not reported in the last 14 days, watchlist hits first. The digest is committed to this public repo, so it deliberately carries **no balances**; the site computes "how to pay" locally.

### Which cash fare an award is compared against

- **Nonstop award** (confirmed from the flight details, never seats.aero's `direct` flag, which only means "one flight number") → cheapest fare with **at most one stop**. Not the cheapest nonstop: one-way nonstop fares on legacy carriers are often several times the one-stop fare (JFK–ZRH Swiss business $8,919 vs $1,468 with one stop) and would produce fake 10¢+ "deals".
- **Connecting award** → cheapest fare, any stops.
- **First class** → valued against the **business** fare. Google's "first" results are business or mixed-cabin on most routes.
- The nonstop fare and the award airline's own fare are shown for context, never used for CPP.

### What counts as a standout (per program, not per cabin)

Each program's points are worth a different amount, so one flat bar rewarded programs whose points are simply worth more — a routine 88,000-mile United award cleared a flat 2.0¢ "business" bar. Instead:

- **Baseline** — what a point in that program is typically worth (`program_values.json`, conservative published valuations, e.g. United 1.2¢, Aeroplan 1.5¢, Alaska 1.6¢). Re-check a few times a year.
- **Standout bar** = baseline × 1.6 in economy, × 1.25 in premium cabins (whose baseline already includes the premium uplift), with a floor of 1.5¢ economy / 1.8¢ business. At or above it → **Book**. In practice: United business 2.10¢, Aeroplan business 2.62¢, JetBlue economy 2.08¢.
- **Skip** = worth less than the baseline: you'd do better spending those points the usual way.
- **Ranking** = dollars of value above the baseline: `(cash − taxes) − points × baseline`, nudged by how unusual the price is for that route (see history below) and discounted for long routings, mixed cabins, airport changes, stale data, awards we couldn't re-verify, and awards whose taxes seats.aero didn't report. One program can fill at most 6 places in economy and 6 in business/first.
- **Only one card per award** — the same program/route/cabin can qualify for several sections; it appears once, in the most useful one (miles you hold → watchlist → top deals), and the dates it beat are folded into "also available".
- **One fare per route** — cards for the same route and cabin within 21 days are all valued at the cheapest fare found among them. Fares genuinely move day to day, but whichever card drew the high fare would otherwise look like the better deal.
- **What wasn't checked is said out loud** — every published deal is re-verified against seats.aero (flights, stops, seats, current price). When the daily quota or the pass budget runs out, the card says "flights and seats not confirmed" and is ranked down, rather than being shown as if it were confirmed.
- **Route price history** (`award_history.py`) — every scan records the cheapest award per program/route/cabin/travel-month, free, so after ~10 days a card can say "cheapest this route has been in the last N days". Ranking by dollars alone mostly measured deal size.
- **Return legs** — a reported one-way is paired with a real return award from the same scan where one exists, with the round-trip points total.

Set in `valuation.py` (`baseline_cpp`, `great_floor`, `verdict_for`) and used everywhere: Top Deals, Flight Search, emails. Watchlist entries can still set their own `bar`.

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
- **Transfer bonuses:** add active ones to `transfer_bonuses.json` (pool, partner, bonus %, end date). "How to pay" then uses the bonus (e.g. Chase → Aeroplan +20% needs ~17% fewer Chase points). Expired entries are ignored.
- **Programs:** follow your point pools automatically (Chase UR, Wells Fargo, and Bilt, whose balance can be transferred without an open card). Getting a new card (e.g. Capital One Venture X): change its `status` from `"planned"` to `"held"` in `cards_data.py`; its pool becomes active and its airline partners that seats.aero covers are scanned from the next run. `python deal_finder.py --include-planned` previews that without changing anything.

---

## Accuracy notes

- **CPP** = `(cash fare − award taxes in USD) / points × 100`. Taxes are converted with a live ECB rate (frankfurter.app), static fallback offline. seats.aero API taxes are in cents.
- **Cash fares** are for one adult and not necessarily on the award's airline (see matching rules above). Deals near the top are valued at the lower of the one-way fare and half a 7-night round trip; deals further down and "other dates" use the one-way fare, so their CPP can read high.
- **Award data** is seats.aero Cached Search (updated every few days, not live). Results older than 10 days are dropped. seats.aero allows 1,000 API calls a day and one scan uses ~210; if the quota runs out mid-scan the run keeps what it has and says so.
- **Coverage is a subset, deliberately:** ~150,000 awards are scanned, but only the ones the estimate rates plausible get a real fare (roughly a third), so a bargain on a route the model misjudges can still be missed. Each daily scan also records the cheapest award per program/route/cabin/month in `award_history.json`, which will make "cheapest this route has been in 90 days" possible. Always confirm on the airline's site before transferring points — transfers are irreversible.
- **Coverage:** seats.aero covers Aeroplan, Flying Blue, BA, Iberia, JetBlue, Singapore, United, Virgin Atlantic, Qatar, Turkish, Etihad, Qantas, Finnair, Aeromexico and more, but not LifeMiles, Asia Miles, TAP, EVA, Aer Lingus or hotel programs.
- **Transfer partners** in `cards_data.py` were last verified 2026-09-16; known source conflicts are listed in its header.
- **Streamlit Cloud storage isn't durable:** `balances.json` / `program_balances.json` reset when the hosted app restarts.
- **The repo is public:** `deal_digest.json`, `cash_quotes.json` and `deal_log.json` hold award and fare data only — no credentials and no balances. `scan_config.json`'s watchlist does reveal which destinations and seasons you're watching. `deal_log.json` is the retired alert pipeline's history, kept because its recorded fares train the fare model.

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
| `valuation.py` | CPP math, currency conversion, per-program baselines and verdict (`program_values.json`) |
| `funding.py` | How to pay for an award: held miles first, then the best transfer (with active transfer bonuses) |
| `award_trips.py` | Flight-level detail and live re-verification: flights, cabin per leg, stops, seats, current price, airport changes, booking link |
| `seats_aero.py` | Single-date award search for Flight Search; program name mapping |
| `cards_data.py` | Cards, point pools, transfer partners |
| `ledger.py` | Card balances and miles held in programs (secret Gist when GIST_TOKEN is set, else local files) |
| `places.py` | City, country and airline names for codes (`airport_coords.json`, `airline_names.json`) |
| `digest_view.py` | Deal cards on the site |
| `deal_email.py` | Digest email (Gmail SMTP) |
| `airports.py` | City/airport dropdown data |
