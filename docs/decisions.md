# Design decisions

Why the Deal Finder values and ranks deals the way it does. Newest first. Each entry says
what was decided, why (with the numbers that drove it), what was rejected, and how to
revisit it. Code comments point here; change the entry when you change the decision.

---

## 2026-10-23 — Show exceptional earlier deals again, in their own section

**Decision.** The email gets a last section, "🔁 Still available from earlier emails"
(`pick_still_available`). It lists up to 3 cards (`STILL_AVAILABLE_MAX`) that were emailed
before and are still on today's list, re-checked today, and still pass every email rule
(`worth_emailing`) at **1.5× their bar** or better (`STILL_AVAILABLE_MULTIPLE`). The best go
first. A card goes in only if it was last emailed, or last shown here, **7+ days** ago
(`STILL_AVAILABLE_GAP_DAYS`), so each one shows at most once a week. The section only goes
out with an email that has at least one new deal, so there's never a reminders-only email.
It doesn't change `reported_routes`: the 14-day repeat rule works as before. The digest
records `still_available_shown` and marks those cards `reminded`; `make audit` shows them
as `AGAIN` and `make audit-live` re-prices them.

**Why.** The user (Oct 23): "Should we recycle old listings in emails that are exceptionally
good if they're still there?" That day 1 card was emailed, and 16 of the 29 site cards were
held back as repeats. 6 of those were at 1.5× their bar or better (JFK–LHR Virgin premium
economy 10,500 points at 2.15×, JFK–PTY Flying Blue business 23,500 at 1.73×). One email is
easy to miss, and the inbox never says a deal is still bookable. Replay over Oct 15–23:
6 cards shown again in 9 emails, 0–2 per email (Oct 23 would have shown EWR–JNB United
premium economy 55k, 1.60×, first emailed Oct 16).

**Rejected.** Dropping the 14-day repeat rule: before Oct 5, 49% of emailed cards repeated
a trip already sent, and they read as new deals. A separate weekly email: one more thing to
read. A lower 1.25× multiple: it would fill the section daily with ordinary deals.

**Watch.** `make audit`'s `still available:` line. If the section is the same 3 cards every
week, lengthen the gap. The "no repeat emails" test in the 5-clean-days goal now means
"no repeats outside this section."

---

## 2026-10-21 — Only departures 30+ days out

**Decision.** `scan_config.json` `min_days_out` 3 → 30. The daily scan (and the roadmap
preview, which reads the same config) asks seats.aero only for departures 30–330 days out, so
close-in dates leave the email, the site and the watchlist. Flight Search and the return-leg
scan pass their own dates and are unaffected.

**Why.** The user (Oct 21): "I can't recall a time when I booked an international flight a
week out"; a month or more is realistic. Oct 6–21, of 83 emailed cards 11 left within 7 days,
22 within 14, 28 within 21 and 35 within 30 (42%). The 30-day cutoff also covers 3 of the 5
emailed cards that fell below their bar a day later (JFK–NRT 18 days out, JFK–PUJ 24 and 22):
close-in fares swing. The scan's call count doesn't depend on the window, and the selection
slots and Google lookups close-in dates used now go to later dates.

**Rejected.** 14 days (27% of emails, none of the 5 that fell) and 21 days (34%, 1 of 5); the
user chose 30. Filtering only the email: close-in cards would still take shortlist slots and
lookups for trips the user won't book.

**Watch.** The email count may dip for a few days; `make audit`'s card count shows it. Fare-model
training and `award_history` no longer see close-in dates, which is fine as long as nothing
is scored there.

**Revert:** set `min_days_out` back to 3 (and update `test_daily_scan_skips_departures_under_30_days_out`).

---

## 2026-10-19 — Email a deal only if it also clears its bar at the route's usual fare

**Decision.** Each card now carries `cpp_at_typical`: its CPP at the lower of today's fare and
the route's usual fare (`Scored.typical_fare`, the fare model's median, known only when it has
3+ real fares on that route and cabin). `worth_emailing` requires `cpp_at_typical` ≥ the bar.
The 10% `EMAIL_MARGIN` still applies to today's fare only. With no usual fare known, today's
fare decides, as before. Cards that fail stay on the site with a note ("At this route's usual
$X fare it's Y¢/pt, under the bar: only worth it if you must fly this date"). Email and site
cards show the usual-fare CPP whenever today's fare is above the usual one. The card's
headline CPP and the bar itself are unchanged.

**Why.** The user (Oct 19) noticed deals looking great because the cash fare that day was
expensive; a flexible traveler would pay cash on another date. Replay of all 67 emailed cards
Oct 6–19: 11 were under the bar at the usual fare. That includes 3 of the 5 that fell below the
bar at the next day's fares (JFK–NRT Oct 9, JFK–GCM Oct 13, JFK–PUJ Oct 16). The other 2 can't
be caught this way: EWR–HKG's fare was already below usual, and JFK–PUJ Oct 18 was the
old-fare bug fixed that day. The 8 others "held" at the next day's fares, but mostly because the
fare stayed high (EWR–LIM business 2.47¢ on $1,518; usual $861 → 1.38¢).

**Rejected.** *Average of today's and the usual fare*: withheld 4 and caught 2 of the 3 (missed
JFK–NRT, the clearest spike, $3,540 vs $2,895). *Requiring the 10% margin at the usual fare
too*: holds back more cards for no fall it would have caught. *Ranking or the bar on the usual
fare*: ranking already caps spikes at 1.25× (2026-10-05), and the CPP shown should stay the real
one for anyone who must fly that date.

**Watch.** The usual fare can be wrong: for EWR–NRT premium economy the model says $1,200, but
on Oct 19 every date sampled Nov–Jan was $1,300–1,670 one-way (≤1 stop). A too-low usual fare
withholds a good deal from the email. `make audit` prints `usual fare: N card(s) under the bar
at the route's usual fare, not emailed`. For two weeks, check those against live fares on other
dates. If they're often fine, the fare model's medians need work before this rule stays.

**Revert:** `git revert` the commit titled "Email only deals that clear the bar at the usual fare".

---

## 2026-10-18 — Audit flags cards priced on an earlier day's fare (and two rules held back)

**Decision.** `make audit` now checks each published card's exact-date fare in the
`cash_quotes.json` committed with the digest. A card whose fare was fetched more than 6 hours
before the run (`OLD_FARE_HOURS` in `tools/audit_digest.py`) is marked `OLD FARE (date)` if it was
emailed (counts as a problem) or `old fare (date)` if it's site-only (shown, not counted:
the fresh re-price keeps a saved fare when Google's answer comes back empty). A summary line
reads `fares: N of M published card(s) on an earlier day's fare`. Audit-only: the daily run,
the email and the site are unchanged.

**Why.** It turns the check of the fix above into a routine one instead of a manual one.
Replayed: Oct 17 had 15 of 30 published cards on an old fare; Oct 18 had 10 of 32, including
3 of the 6 emailed (JFK–PUJ from Oct 16, JFK–HND from Oct 17, JFK–PTY from Oct 13). From Oct 19 the
emailed count should be 0 and the total near 0.

**Revert:** `git revert` the commit titled "Audit: flag cards priced on an earlier day's fare".
It touches only `tools/audit_digest.py`, one test in `test_deal_finder.py`, this entry and CLAUDE.md.

**Held back, with the data, for later:**
- *Minimum dollar cushion for emailing.* The cushion is how far the fare can fall before the
  card drops below its bar: (CPP − bar) × points. Oct 6–18, cards under $100 of cushion: 9 emailed,
  3 fell (JFK–GCM, JFK–PUJ ×2); $100 or more: 54 emailed, 2 fell (NRT, HKG, both under the 10%
  margin that `EMAIL_MARGIN` now withholds). Cheap Caribbean economy fares move ~$75 a day. Not
  shipped because today's fall is fully explained by the old fare (fixed above), and testing two
  changes at once would hide which one worked. **Ship it** (≈"email only if cushion ≥ $100") if
  a card with an old-fare-free audit still falls with under $100 of cushion.
- *Minimum lead time for emailing.* 8 emailed cards left within 8 days (MAD, IST, CMN, PMO 3–4 days
  out); none fell, and some were among the best values (CMN 3.44¢). No data for it; it's a
  question of whether the user can travel on short notice. Ask before adding one.

---

## 2026-10-18 — Don't email long itineraries

**Decision.** A card flagged `slow` (the trip takes more than ~5–6 h longer than flying the
distance nonstop; see `Scored.slow`) is not emailed (`worth_emailing`). It stays on the site with
its "Long itinerary" label, and keeps the 25% ranking and within-card discount.

**Why.** Oct 6–18, 27 of 63 emailed cards were slow; 12 had roughly 10+ hours on the ground
(EWR–PVG Aeroplan via Delhi, 44.8 h; EWR–CMN United via Geneva and Frankfurt, 25.8 h). The 25%
discount only reorders the top list; it never kept a slow card that cleared its bar out of the
email. The user: no savings are worth a 15-hour layover.

**Rejected.** Only skipping ~8 h+ layovers (would have cut 13 of 63), or a harsher ranking
discount alone. The user chose the strictest option.

**Revisit** if the email gets too thin, or a faster option on the same card is being missed
because a slow award leads it (the within-card discount is still 25%).

---

## 2026-10-18 — Published cards carry a fare from this run

**Decision.** Before publishing, each card is re-priced on its exact date unless its saved quote
is at most 6 hours old (`FRESH_QUOTE_MAX_AGE_DAYS`), i.e. from this run. An empty answer to that
lookup keeps the card's existing fare and doesn't overwrite the saved quote.

**Why.** The "fresh re-pricing" step reused any exact-date quote within the normal reuse age
(2 days for trips ≤30 days out, else 7). Oct 7–18, 10–22 of ~30 published cards a day carried an
earlier day's fare. Oct 18 emailed JFK–PUJ JetBlue 5,000 at Oct 16's $229 (3.47¢); Google said
$154 (1.97¢, bar 2.08) twenty minutes after the run. Costs ~10–20 extra lookups a run.

**Revisit** if run time nears the 30-minute timeout, or throttled days leave many cards on an
old fare (count "unavailable" in the `Fresh re-pricing` log line).

---

## 2026-10-13 — Email a deal only when it clears its bar by 10%

**Decision.** A card is emailed only if its CPP is at least `EMAIL_MARGIN` (1.10) × its bar
(the watchlist bar for watchlist cards). Thinner cards are still listed on the site, and are
not recorded as sent, so the same trip can be emailed later once its margin grows. The
repeat rule (15% more CPP or 10% fewer points) is unchanged.

**Why.** Re-pricing emailed cards at the next day's fares, Oct 6–13: 3 fell below their bar
(JFK–NRT Flying Blue, 9% over when emailed; EWR–HKG Aeroplan, 7%; JFK–GCM American, 3%).
All 3 had cleared the bar by under 10%; none of the cards with a 10%+ margin fell. The rule
would have held back 8 cards in those 8 days, the 3 that fell plus 5 that held (JFK–SJU and
JFK–SXM JetBlue, EWR–RAK United premium economy, JFK–DEL Aeroplan, JFK–BGI Alaska).

**Rejected.** "Below the bar at the route's typical fare": it would have held back 7 cards,
2 of which fell, and it misses EWR–HKG (fare below typical). Requiring both conditions held back 3, 2 of which fell, but
also missed HKG. Raising the bars themselves would hide these deals from the site too.

**Revisit** after ~2 weeks: count withheld cards that would have held vs. emailed cards that
still fell. If thin cards hold far more often than 5 in 8, lower the margin; if a 10%+ card
falls on a fare move, look at the fare relative to typical before raising it.

---

## 2026-10-05 — Rank on a capped fare; show the real CPP

**Decision.** For *ranking* only, a deal is valued at the lower of today's cash fare and
`FARE_SPIKE_CAP` (1.25) × the route's typical fare (`Scored.rank_cash`). The CPP on the card,
the "$ better" figure and the bar a deal must clear all keep using the real fare. When the
cap applies, the card says so: *"today's fare is 1.6x what this route usually costs (ranked as
if it were $3,454)"*. The subject line headlines the biggest deal by ranked value.

**Why.** A great CPP can come from a cheap award or from an expensive fare. Close to
departure it is often the second: across 151 cards emailed Sep 27 – Oct 5, fares within 14
days of departure ran a median 1.29× the route's typical fare, and 38% were over 1.4×.
EWR→ATH on Emirates led the list at 5.77¢ on a $3,840 fare that is usually $2,260, four days
out. If you were flying that day anyway the saving is real, which is why the deal is kept and
its CPP shown — but it isn't the bargain its rank implied.

**Scope.**
- "Typical fare" is the fare model's estimate for the route and cabin (`fare_model`), used only
  when it is built from ≥ `MIN_ROUTE_FARES_FOR_CAP` (3) real fares on that exact route+cabin.
  Distance-based guesses for unseen routes, and thin premium-economy data, can be off by 30%+
  (an Iceland premium-economy fare read as "2.6× typical" was model error), so no cap there.
- Applies to ranking between cards (`rank_value`, top-list and watchlist order) and to which
  date/program leads a card (`choice_value`). Two dates that both hit the cap tie; the tie goes
  to the less inflated fare, so a card shows the date that's good at a normal price.
- It is a cap, not a penalty: a spiking date still beats a date whose fare is below the cap.

**Effect when introduced.** Replayed over Sep 30 – Oct 5: 20 of 95 top-list cards capped.
EWR→ATH 1.7–1.8× moved #1 → #4; EWR→TLV 1.4× #2 → #6–8; JFK→VIE 1.6× #3 → #9. EWR→VIE stayed
#1 capped — still the best deal at a normal fare.

**Rejected.**
- *Discard or penalise spikes.* The saving is real for someone flying that date; hiding it
  throws away information. A cap only removes the part of the value that came from the spike.
- *Rank by total points spent.* A different question ("how big a dent in my balance"); a mild
  points preference could be added later on top, but it doesn't address last-minute fares.
- *Lead time as a proxy.* Spikes also happen months out (46–120 days: 25% of cards over 1.4×,
  peak dates); comparing with the route's own typical fare catches both.

**Revisit if** the fare model's route coverage changes a lot, or audits show capped deals the
user would have wanted higher. Knobs: `FARE_SPIKE_CAP`, `MIN_ROUTE_FARES_FOR_CAP`
(`deal_finder.py`).

---

## 2026-10-05 — Don't re-email the same trip because its date changed

**Decision.** Email history is also kept per program + airports + cabin (`reported_routes`).
Within the 14-day cooldown a trip is emailed again only if it is materially better: ≥15% more
CPP or ≤90% of the points. The site still lists everything, marking repeats "Sent before".

**Why.** Sep 27 – Oct 4, 74 of 152 emailed cards repeated a trip already emailed (JFK→ANU on
American went out on 5 different days); the old key included the date and points. Replayed:
86 of 152 would have gone out, and the 8 repeats kept were real improvements.

---

## 2026-10-02 — Cap how many options each card checks

**Decision.** After `MAX_EXPLORE_PER_CARD` (2) awards on a card have been checked on
seats.aero, its unchecked options carry the unverified discount when choosing the leader.

**Why.** Checking only ever reveals flaws (stops, detours), so an unchecked option always
looked slightly better than the one just checked; each check promoted the next. Runs spent
117–155 lookups and ran out of rounds. A test with 4 cards × 6 options reproduced it (24
lookups, 7 rounds, same 4 awards published); capped: 8 lookups, 3 rounds. Two earlier
attempts (0ec75fb, 7c7b74f) treated symptoms without a reproducing test and didn't hold.

---

## 2026-09-29 — Prefer a nonstop within a card

**Decision.** Which date/program leads a card is chosen by `choice_value`: value after the
quality discounts, and a confirmed connection must be worth 15% more than a nonstop
(`CONNECTION_MARGIN`).

**Why.** Money alone let EWR→VIE lead with a Kraków connection over the still-bookable Austrian
nonstop on an $18 difference.

---

## 2026-09-28 — Choose between programs by the points you'd actually spend

**Decision.** Within a card, options are compared on value minus what you'd give up: held miles
at that program's usual value plus card points to transfer (bonuses applied) at the card's
typical value (`funding.opportunity_cost_usd`). Between cards, ranking still uses each program's
own baseline.

**Why.** EWR→VIE showed United 88,000 (83,000 Chase) over Aeroplan 75,000 (63,000 Chase with the
bonus) only because United miles have a lower "usual value".

---

## 2026-09-27 — Never value an award at the full one-way fare

**Decision.** A deal is valued at the lower of the one-way fare and half a round trip. If no
round trip can be priced, it is valued at 81% of the one-way fare (`RT_HALF_ESTIMATE_RATIO`,
the median over 210 deals where both were priced), flagged, ranked down, and the email warns
when more than 30% of deals rely on the estimate.

**Why.** Google returned no round-trip fare for 40 of 48 checks one morning; every deal was then
valued on its one-way fare, the subject quoted Cape Town at 6.2¢ (really 3.6¢), and six deals
cleared bars they would have missed.
