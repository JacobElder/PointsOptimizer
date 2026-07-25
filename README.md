# PointsOptimizer ✈️

A personal toolkit for deciding **when to redeem airline/credit-card points and when to hoard them**, and for surfacing genuinely great award deals out of the flood of alert noise.

The core question it answers: *a redemption in front of you is worth some cents-per-point (CPP) today — is that good enough to book now, or are you better off holding those points for a higher-value trip later?* Around that core sit live cash-price lookups, live award-availability search, a multi-card wallet, and an automated "Deal Radar" that scores your seats.aero email alerts against real cash prices and emails you only the standouts.

---

## Quick start

```bash
cd ~/Documents/GitHub/PointsOptimizer
pip install -r requirements.txt
streamlit run app.py
```

The app is also deployed on Streamlit Community Cloud (auto-redeploys on every push to `main`). Viewer access is restricted to the owner's email.

### Configuration (optional but recommended)

Live lookups need API keys. Copy the example secrets file and fill in what you have:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

| Key | Used for | Where to get it |
|-----|----------|-----------------|
| `SERPAPI_KEY` | Live **cash prices** (Google Flights via SerpApi) | serpapi.com — free tier is **250 searches/month** |
| `SEATS_AERO_API_KEY` | Live **award availability** | seats.aero Pro — **1,000 Cached-Search calls/day** |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Deal Radar **email alerts** (Gmail SMTP) | Google Account → Security → App Passwords |

`secrets.toml`, `balances.json`, and `history.csv` are all gitignored and never leave your machine. On Streamlit Cloud the same keys go in **Settings → Secrets** (TOML format).

Everything degrades gracefully: without keys, the app falls back to manual entry and the automation simply doesn't run.

---

## The pages

| Page | What it does |
|------|--------------|
| **Home** (`app.py`) | The redeem-or-hoard decision. Enter a redemption (cash price, points, taxes) and it computes today's CPP, then runs a Monte Carlo simulation of future point value to recommend **redeem now** vs. **hoard**. |
| **1 · Wallet** | Your card/points portfolio — balances per pool, transfer partners, and which pools are active vs. locked. Balances persist locally in `balances.json`. |
| **2 · Flight Analyzer** | Point a route/date/cabin at it and it pulls a **live cash price** (SerpApi) and **live award availability** (seats.aero), computes CPP, and gives an instant verdict. Live search is the default path. |
| **3 · History** | Log of past redemptions/decisions (`history.csv`) plus calibration of the simulation against what actually happened. |
| **4 · Roadmap** | "What-if" for your card roadmap — model opening/closing cards and how it shifts your points position. |
| **5 · Deal Radar** | The automated pipeline's dashboard (see below). Every seats.aero alert that's been scored shows here, with sort/filter, a highlighted "act now" section, a return-flight finder, and on-demand round-trip valuation. |

---

## Deal Radar — capture alerts, price on demand

seats.aero emails you whenever award space opens on a route you've set an alert for. The problem: **most of those alerts clear seats.aero's own points/fee filter but aren't actually good value** in CPP terms. Deal Radar is the needle-in-the-haystack filter.

**Current mode (chosen 2026-07-25): on-demand.** A cloud routine still *captures* every seats.aero alert into `deal_log.json` (free — Gmail only, no pricing). But pricing is **manual**: on the Deal Radar page you click **💵 Price this deal** on any captured deal to spend one live SerpApi cash-price lookup and get its real CPP. This keeps a browsable list of every alert while spending the scarce SerpApi quota only on deals you actually care about — no schedulers, races, quota drain, or notification noise.

```
  ┌─ CAPTURE (cloud, always on) ─┐        ┌─ PRICE (on demand, in the app) ─┐
  │ Scheduled claude.ai routine   │  git   │ Deal Radar page                  │
  │ reads seats.aero alert emails │ ─────► │ • click "Price this deal"        │
  │ via Gmail, parses, queues to  │ shared │ • one live cash price (SerpApi)  │
  │ deal_log.json `pending`       │  file  │ • CPP + cabin-aware verdict      │
  └───────────────────────────────┘        └──────────────────────────────────┘
```

The **automatic** pricing+email pipeline (`price_pending_deals.py` + `.github/workflows/deal_radar_pricing.yml`) is fully preserved but **turned off** — the workflow is `workflow_dispatch`-only. See [Re-enabling automatic email alerts](#re-enabling-automatic-email-alerts) below.

### What counts as a "standout" (cabin-aware thresholds)

A deal is a standout (and, when auto-alerts are on, emailed) only if its CPP clears the bar for its cabin — premium cabins commit far more points, so they need a higher return. The same cabin-aware bar drives the on-page BOOK badge, so they never contradict:

| Cabin | "Standout" / BOOK CPP floor |
|-------|----------------------|
| Economy / Premium Economy | **≥ 1.5¢/pt** |
| Business / First | **≥ 2.0¢/pt** |

(Below **1.0¢** is 🔴 SKIP; in between is 🟡 BORDERLINE.)

### Using the Deal Radar page

- **Summary metrics** up top: captured (unpriced) count, priced count, standouts, best CPP.
- **📋 Captured** section: every unpriced alert, with cabin filter and sort (date / points / program / cabin). Click **💵 Price this deal** on one → live CPP + verdict badge inline, one SerpApi lookup per click.
- **🔁 Find a return flight** (after pricing a deal): searches seats.aero award space on the *reverse* route over a return-date window. Effectively free — one Cached-Search call, well within the 1,000/day quota.
- **💵 Value this return** (per return option): pulls a live cash price for that return leg and shows its CPP **plus a full round-trip total** (combined points, taxes, cash, blended CPP). Another explicit-click SerpApi lookup.
- **Previously priced deals**: history of anything priced earlier (e.g. from when auto-pricing was on), with full sort/filter and a 🎯 standout section.
- On-demand pricing is **per session** — it shows you the CPP immediately but doesn't write back to the deal log.

### Re-enabling automatic email alerts

Nothing was deleted when we switched to on-demand — the whole pricing+email pipeline is intact and tested:

- **`price_pending_deals.py`** — pricing loop + `_notify_mac` (macOS notification) + `send_deal_alert_email` (batched standout email). Still improved with the monthly SerpApi budget guard, transient-failure retry, and malformed-entry guards.
- **`deal_email.py`** — the HTML email builder + Gmail SMTP sender.
- **`.github/workflows/deal_radar_pricing.yml`** — intact, just switched to `workflow_dispatch` (manual) only.

To turn automatic pricing + email alerts back on (~2 minutes): re-add the `schedule:` (and/or `push:`) triggers to `deal_radar_pricing.yml`. The exact trigger block is preserved in git history at commit `8f3daee` (verbatim: a 4-hourly `schedule` plus a `push` on `deal_log.json`, with the pricing commit carrying `[skip ci]` so it never loops). The cloud capture routine is still running, and the three repo secrets (`SERPAPI_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`) are still set — so re-adding the triggers is all that's needed; it resumes pricing every capture and emailing standouts with no other setup. You can also bulk-price the current queue manually any time from **Actions → Deal Radar pricing → Run workflow**.

---

## Accuracy notes & nuances

- **CPP formula:** `(cash_price − award_taxes_in_USD) / points × 100`. Taxes are converted to USD via a live FX rate (frankfurter.app, ECB-backed, no key), falling back to a static table only if offline.
- **Cash prices** come from Google Flights via SerpApi — a real bookable fare for the same route/date/cabin, not an estimate. Spot-check against Google Flights directly if a number looks off.
- **Award coverage:** seats.aero tracks major alliance/transfer programs (Aeroplan, Flying Blue, Qatar, Virgin Atlantic, United, etc.) but **not every program** — e.g. LifeMiles, Asia Miles, TAP, EVA are absent, and hotel points have no equivalent here. Uncovered results still show under their raw source name rather than being dropped. See the header of `seats_aero.py` for the verified list.
- **Live Search vs. Cached Search:** the Pro tier only exposes Cached Search (recent, not real-time-to-the-second). Good enough for an on-demand "is this worth it?" check; don't treat it as a live seat count.
- **Continuous alerts re-fire:** seats.aero re-sends an alert email whenever a still-available deal is re-scanned, even with no change. Deal Radar dedupes on `(program, origin, dest, cabin, date, points)`, so a repeat of an already-scored deal is correctly ignored rather than re-emailed.
- **Local storage isn't durable on Streamlit Cloud:** `balances.json` / `history.csv` are local files; a Cloud container reset wipes them. Fine for running the analyzer remotely, but don't rely on Cloud for long-term balance storage.

---

## FAQ

**Why did I get a seats.aero alert but no Deal Radar email?**
Automatic emails are currently **off** (on-demand mode) — the alert is captured to the Deal Radar page for you to price on demand, not emailed. If you re-enable automatic alerts (see above), you'd still only be emailed genuinely *new* standouts (a continuous re-fire of an already-scored deal, or one below the cabin-aware floor, is intentionally skipped to avoid noise).

**Why is a "View on seats.aero" link sometimes broken?**
The raw email links are single-use, recipient-bound click-tracking redirects (`c.seats.aero/CL0/…`) that 400/403 when reused. The parser unwraps them to the permanent `seats.aero/i/<id>` form, which is stable.

**Will the return-flight features burn through my API quota?**
Finding returns uses seats.aero Cached Search (1,000/day — effectively unlimited for this). Only **💵 Value this return** touches the scarce SerpApi cash-price quota (250/month), and only when you click it.

**Does anything run when my computer is off?**
Only **capture** — a cloud routine that logs seats.aero alerts to `deal_log.json` every few hours (no machine needed). Pricing is now on-demand from the app, so no automatic pricing runs. (The old Mac LaunchAgent was retired earlier; the GitHub Actions pricing job still exists but is manual-only.)

**Is my data exposed now that the repo is public?**
No credentials or personal financial data. Secrets live only in gitignored files; `balances.json`/`history.csv` were never committed. `deal_log.json` (tracked, needed by the automation) contains only award-deal data you're already monitoring.

---

## Development

```bash
python3 -m pytest -q          # full suite
python3 -m pytest test_simulation.py -v
```

Tests run automatically on every push/PR via `.github/workflows/tests.yml`. Network calls are mocked, so the suite is fast and offline-safe.

### Module map

| Module | Responsibility |
|--------|----------------|
| `simulation.py` | Monte Carlo redeem-vs-hoard valuation engine (NumPy) |
| `flight_search.py` | Live cash prices (SerpApi Google Flights) |
| `seats_aero.py` | Live award availability (seats.aero Cached Search) |
| `seats_aero_alerts.py` | Parse seats.aero alert emails → structured deals |
| `check_alerts.py` | Batch cash-price lookup + CPP + verdict; live FX |
| `deal_log.py` | Shared `deal_log.json` state, dedup keys, standout thresholds |
| `price_pending_deals.py` | The pricing half of the pipeline (GitHub Actions / local) |
| `deal_email.py` | HTML deal-alert email builder + Gmail SMTP sender |
| `cards_data.py` | Static cards / pools / transfer partners |
| `ledger.py` | Local balances (`balances.json`) + history (`history.csv`) |
| `award_charts.py` | Curated sweet-spot award chart |
| `going_parse.py` | Paste-to-parse for Going deal emails |
