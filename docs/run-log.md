# Run log

Day-by-day audit notes for the daily Deal Finder run, moved out of `CLAUDE.md` on Nov 15 2026 so
that file stays short. **Oldest first, newest last** — append new days at the bottom. Rule
rationale and tallies live in `docs/decisions.md`; what to watch next is in `CLAUDE.md`
("Where things stand"). Never write balances here (public repo).

- **Streak history (as of Nov 14):** 0 of 5 (Nov 14 fresh-fare retry restarts it; Nov 8–14 had OLD FARE emails, see below) (the 60-day route memory shipped Nov 7 restarted it; Oct 26–Nov 7 had no code change
  but re-sent 18 unimproved deals as new, so not counted). (Oct 24–25 clean.) (Oct 22–23 were clean; the "Still available" email section shipped Oct 23 evening restarted it). (Oct 20–21 were clean; the 30-day lead-time change on Oct 21 evening restarts it, as the usual-fare rule did). (Oct 19 was clean, but the usual-fare email rule shipped that evening restarted it.) (Oct 18: an emailed card fell below bar and a code fix shipped; Oct 17's one-way retry fix restarted it; Oct 17's own run was clean). (Oct 14–15 clean; Oct 16 reset it: JFK–PUJ fell below bar.) (Oct 12 was clean; Oct 13 reset it: an emailed deal fell below its bar.) (Oct 6–8 were clean, but the user decided on Oct 13 to count conservatively: Oct 9 (deal fell below bar) and the code fixes on Oct 10 and 11 reset the streak.) Fixes from Oct 5 (repeat suppression, fare-spike cap,
  Emirates cabin links, test isolation of balance files, runner pin) had their first real run Oct 6
  and worked. Oct 7's digest came from the hand-clicked cron-job.org test (01:53 UTC), so the
  unattended 10:00 ET dispatch has not yet produced a digest on its own (see layer 2 above). Oct 8
  came from GitHub's own schedule (15:36 UTC); 7 emailed, all above bar live, one intended repeat
  (EWR–MAD Alaska, 70k → 55k points). Flight-detail lookups 91 (Oct 7) and 93 (Oct 8), above the
  ~50–80 norm and far under the 260 cap, tracking more round-trip checks (~790 vs 562) — fine
  unless it keeps climbing. Borderline margins on Oct 8: JFK–BGI 2.76¢ vs 2.71 bar, JFK–SJU 2.12¢
  vs 2.08 (the parked "small margin above the bar" idea).
- **Oct 9: not clean by the letter, no code fault.** GitHub schedule (15:18 UTC), healthy log (74
  flight lookups, 507/510 round trips priced, all selections ≤7 rounds), 5 emailed, 0 repeats, 0
  offline problems. JFK–NRT Flying Blue 115k+$699 (Oct 27) fell below its bar at live fares
  6 h later: $3,540 → $2,949, 2.47¢ → 1.96¢ vs 2.27 bar. The email fare was 1.22× the route's
  typical $2,895 (the fare model's estimate, which was right) and the margin was only 9%. Replay
  of Oct 6–9: 3 emailed cards would have been below the bar at the typical fare (JFK–CPT Oct 6,
  EWR–MUC Oct 7, JFK–NRT Oct 9); only NRT actually fell. 1 of 3 isn't enough to ship a rule. If
  another "fare well above typical + thin margin" card falls, that's the data for the parked
  margin idea. Counted as a reset (user, Oct 13).
- **Oct 10: emailed deals clean; one code fix.** GitHub schedule (14:28 UTC), 3 emailed, 0 repeats,
  0 offline problems, all 3 above bar live on Oct 11 (EWR–DXB LifeMiles thin: 3.09¢ vs 3.00 bar).
  Google throttled the runner 14:34–14:36: 94 of 233 cash lookups and 279 of 848 round trips came
  back empty (`::warning`), but every published card still had a real round trip. The empty answers
  were saved as "no fare" and blocked those dates from pricing for 2–7 days; now they're trusted
  only within the run (`NO_FARE_MAX_AGE_DAYS`). Watch Oct 11's `no_fare` and live-lookup counts.
- **Oct 11: no code fault; one emailed deal fell on a fare move.** GitHub schedule (14:54 UTC), 7 emailed,
  0 repeats, 0 offline problems, 0 published cards on estimated round trips. EWR–HKG Aeroplan 102.5k
  (Sep 4 2027) fell below bar live: $2,930 → $2,636, 2.80¢ → 2.52¢ vs 2.62 (7% margin; the fare was
  *below* typical $3,133, so not Oct 9's pattern). Oct 10's fix worked: 39 of the 98 blocked dates were
  re-looked-up, 31 priced. Google throttled again: round trips 404/600 priced (67%, 2nd day running;
  also Oct 4 at 57%), empties peak in the busiest minute (15:02: 78 lookups, 20 empty). Main lookups
  are unpaced; the 2 s-spaced end-of-run retry recovered 8/10. Shipped d1b6669 (unproven until a
  throttled day): pause 3→24 s after an empty answer, 240 s max per run; read the new `Google lookups
  by minute` / `Paused` log lines — if the priced share recovers once pauses start, it works. Thin-margin tally (<10% over bar, Oct 8–11): 7 emailed, 2 fell
  (NRT, HKG); none of the wider-margin cards fell.
- **Oct 12: clean, no code change.** GitHub's morning slots and cron-job.org both missed again; the Claude
  routine started it (16:06 UTC, push event). 4 emailed, 0 offline problems, all 4 above bar live on Oct 13
  (lowest margin: EWR–JNB United PE 2.41¢ vs 1.80). One repeat by the rule: JFK–ZRH Aeroplan 60k, 3.45¢ on
  Oct 8 → 3.99¢ (1.157×, just over 1.15); live it's 3.61¢, so the "better" was mostly a fare bump. 74 flight
  lookups, selections ≤6 rounds, `no_fare` 31. Round trips 483/584 priced (83%, up from 67%), but the
  pauses don't explain it: 50 pauses used the whole 240 s, avg 4.8 s, so mostly single 3 s pauses on
  scattered one-way empties (16:12–16:18) and the cap was gone by ~16:20, before most round trips; minutes
  with pauses (16:19: 79%) priced no better than without. Undecided, not proven. Streak: 1 of 5.
- **Oct 13: not clean, no code fault.** GitHub schedule started it at 15:58 UTC; the routine's push run
  (16:05) queued behind it and skipped correctly; the first digest push was rejected by the routine's commit
  and the retry loop recovered. cron-job.org missed again. 9 emailed, 0 offline problems, 67 flight lookups,
  selections ≤5 rounds, `no_fare` 25, round trips 429/520 (82%). 1 repeat by the rule: EWR–RAK United
  business 88k 3.26¢ (Oct 9, Nov 22) → 90k 3.91¢ (Oct 25), 1.20×; held live. **JFK–GCM American 10k fell
  below bar live:** $237 → $185, 2.31¢ → 1.79¢ vs 2.24 (3% margin; fare 1.12× typical $212, 2.06¢ at
  typical). Replay of all emailed cards Oct 6–13: "below bar at the typical fare" caught 7 cards, 2 fell
  (NRT, GCM); "<10% over bar" caught 8, 3 fell (NRT, HKG, GCM); both together caught 3 (NRT, GCM, BGI), 2
  fell. No card with ≥10% margin has fallen. The user chose the 10% margin: shipped Oct 13 as
  `EMAIL_MARGIN` (email only; thin cards stay on the site). From Oct 14, `make audit` lists no emailed
  card under 1.10× its bar; tally thin cards withheld vs. emailed cards that still fall (decisions.md).
- **Oct 14: clean, no code change.** GitHub schedule (13:42 UTC). 5 emailed, 0 offline problems, all 5 above bar
  live the same day (lowest: JFK–IST Aeroplan 75k 3.15¢ → 2.82¢ vs 2.62 bar). 2 repeats, both by the rule:
  EWR–IST Turkish 65k 3.05¢ (Oct 10) → 3.97¢ (1.30×); JFK–PTY United 38.5k 2.45¢ (Oct 13) → 3.03¢ (1.24×).
  **Worst Google throttling yet:** 130 of 234 one-way lookups empty (`::warning`), round trips 256/571 priced
  (45%; 67% Oct 11, 83% Oct 12–13). Throttled from the first minute (13:48: 6/16). All 30 published cards still
  have a real round trip (0 `rt_unavailable`), so values weren't inflated — the cost is deals never priced.
  d1b6669's pauses: verdict now "doesn't help". The 240 s cap was spent by 13:54 in the one-way phase, priced
  share didn't recover while pausing (13:52–13:54: 48%, 45%, 44%), and the round-trip phase ran unpaced at ~44%.
  If throttling keeps getting worse, the next thing to try (with a test first) is pacing round trips instead of
  pausing after empties; not shipped. Same day, at the user's request: the digest now records
  `google_coverage`, `make audit` prints it with `THROTTLED`, and a throttled run's email opens with a
  line saying coverage was partial. A visibility change, not a fix for a fault, so not a streak reset.
- **Oct 15: clean, no code change.** GitHub schedule (13:25 UTC). 4 emailed, all above bar live on Oct 17. Round trips
  378/715 (53%), THROTTLED. One site-only card (EWR–DEL) on an estimated round trip; not emailed.
- **Oct 16: not clean, no code fault.** GitHub schedule (13:14 UTC). 3 emailed; 1 repeat by the rule (EWR–JNB United PE).
  **JFK–PUJ JetBlue 7,200 fell below bar** (checked Oct 17): $229 → $154, 2.41¢ → 1.37¢ vs 2.08. Margin was 16%, so
  the first emailed card at ≥10% margin to fall; its fare was 1.22× typical $187 (1.83¢ at typical, below bar).
  Replay Oct 6–17: 12 emailed cards were below bar at the typical fare; 3 fell (NRT, GCM, PUJ). No card above bar
  at typical has fallen. If one more such card falls, propose "email only if above bar at min(fare, typical)".
- **Oct 17: clean, no code change.** GitHub schedule at 12:11 UTC (earliest yet). 4 emailed, all above bar live same day
  and above bar at typical fare. 2 repeats, both by the rule: EWR–PVG Aeroplan 102.5k 2.90¢ (Oct 9) → 3.55¢; EWR–CMN
  United 88k 2.32¢ (Oct 13) → 3.44¢ (Oct 20 departure, close-in fare). 95 flight lookups (3rd day 80+; watch),
  selections ≤10 rounds, round trips 658/822 (80%) but one-way 135/254 (53%), THROTTLED.
- **Oct 17 fix: one-way empties are asked again.** Up to 60 flight dates whose one-way lookup came back empty
  are re-asked at the end of pricing, 2 s apart (`ONE_WAY_RETRY_MAX`); before, only round trips of deals about to be
  published were retried, so ~half the one-way candidates on throttled days were never priced. The probe re-asking
  a run's empties priced 27 of 30. Read the `One-way retries: N of M` log line. `google_coverage.one_way_empty`
  now counts what is *still* empty after the retry (audit prints "(N on a retry)"). Adds ≤~3 min; runs were
  19–22 min against a 30 min `timeout-minutes` — watch run time. A code change, so the clean-day count restarts.
- **Oct 18: not clean; code fix.** GitHub schedule (12:36 UTC). **Google unthrottled for the first time since Oct 9:**
  one-way 232/234, round trips 736/736. Only 2 one-way empties, so the Oct 17 retry barely ran (0 of 2) — still
  unproven on a throttled day. 95 flight lookups (4th day 80+), selections ≤9 rounds. 6 emailed; 5 held live.
  **JFK–PUJ JetBlue 5,000 (Nov 9) fell below bar again:** emailed at $229 → $154 live 20 min later, 1.97¢ vs 2.08.
  Allowed repeat (Oct 16 was 7,200 pts). Cause was ours: the $229 was Oct 16's saved quote. `reprice_fresh` reused
  any exact-date quote within the normal reuse age (2–7 days); Oct 7–18, 10–22 of ~30 published cards a day were on
  an earlier day's fare. Fix: re-ask Google unless the quote is ≤6 h old (`FRESH_QUOTE_MAX_AGE_DAYS`); an empty
  answer keeps the saved fare. Watch Oct 19: `Fresh re-pricing … N moved more than 5%` should jump from ~1, run
  time (+~1 min), and `make audit`'s `fares:` line should be ~0 (only cards whose fresh lookup came back empty)
  with no `OLD FARE` on an emailed card. Same evening: that check was added to `make audit` (audit-only; replay:
  Oct 18 10 of 32, 3 of 6 emailed; Oct 17 15 of 30). Two email rules held back with their data in decisions.md
  (Oct 18 entry): a ≥$100 "cushion" above the bar (cards under $100: 3 of 9 fell; $100+: 2 of 54) — ship it if
  a card with no old fare still falls with under $100 of cushion; and a minimum lead time — ask the user first.
- **Oct 19: clean, no code change.** GitHub schedule (15:35 UTC; job 22 min). 4 emailed, 0 repeats, 0 offline problems, all 4
  above bar live the same day (lowest margin: EWR–NRT United PE 65k 2.27¢ vs 1.80; EWR–VIE Aeroplan 75k 4.36¢ → 4.06¢ live).
  Oct 18's fresh-fare fix worked: `fares:` 0 of 35 on an earlier day's fare, `Fresh re-pricing` 6 moved >5% (was ~1).
  Oct 17's one-way retry had its first real test: 20 of 24 empties priced on the retry. Google mostly fine: one-way 263/267,
  round trips 528/582 (91%). 85 flight lookups. One `::warning`: the main selection hit its 12-round limit and the
  safety net checked the 1 unchecked deal (19 deals, 51 lookups — normal 42–59, so slow convergence, not quota churn;
  Oct 6–18 used 5–10 rounds). If it hits 12 again, investigate with a reproducing test.
- **Oct 19 evening: usual-fare email rule.** At the user's request: a card is emailed only if it also clears its bar at
  the lower of today's fare and the route's usual fare (`cpp_at_typical`); cards show that CPP when today's fare is
  higher. Replay Oct 6–19: 11 of 67 emailed would have been held back, including 3 of the 5 that later fell (all 3
  that a rule like this could catch). Watch Oct 20: `make audit` prints `usual fare: N card(s) … not emailed`.
  Expect ~1 a day. For two weeks, spot-check those against live fares on nearby dates: the model's usual fare can be
  low (EWR–NRT PE says $1,200, live Nov–Jan $1,300–1,670), which would wrongly hold a deal back.
- **Oct 20: clean, no code change.** GitHub schedule (13:33 UTC; 20 min). 9 emailed, 0 offline problems, all 9 above bar
  live the same day (lowest margin: JFK–AKL Alaska PE 55k, $1,988 → $1,460 live, 3.51¢ → 2.55¢ vs 2.33). 1 repeat by
  the rule: EWR–TLV Aeroplan 75k 3.22¢ (Oct 18) → 62.4k 3.86¢. Google healthy: one-way 244/248 (6 of 10 on the retry),
  round trips 641/665 (96%). 83 flight lookups, selection 11 rounds/50 lookups, no warning. 1 site-only card (JFK–PTY
  watchlist) on Oct 19's fare, not emailed: expected when the fresh lookup is empty. **First day of the usual-fare rule:**
  5 cards under the bar at the usual fare, but 4 were under the 10% margin anyway; it held back 1 card that would
  otherwise have been emailed: JFK–ANU American 10.5k on Oct 30 ($339, usual $269, 2.13¢ vs 2.24). Spot-check: nonstops
  Nov–Feb are $280 on most dates, so the usual fare was right (2.23¢ at $280). Tally so far: 1 held back, 1 correct.
- **Oct 21: clean, no code change.** GitHub schedule (13:48 UTC; 19 min). 7 emailed, 0 offline problems, all 7 above bar
  live the same day. Thinnest: JFK–LHR Alaska business 45k, Oct 27, $1,589 → $1,264 live, 3.47¢ → 2.75¢ vs 2.71. Its
  fare was already *below* the usual $1,644, so not a spike (like HKG Oct 11), and 6 days out. 2 repeats, both by the
  rule: EWR–PMO United PE 50k 2.19¢ (Oct 18) → 3.07¢; JFK–LHR Alaska business 55k (Oct 20) → 45k. Google: one-way
  236/240, round trips 614/650 (94%). 76 flight lookups; selection 7 rounds/45 lookups (down from 11–12). 1 site-only
  card on an old fare (JFK–LHR Virgin PE, fresh lookup empty), not emailed. Usual-fare rule: 3 cards under the bar at
  the usual fare, none would have been emailed anyway (2 under the 10% margin, EWR–LIM slow). Tally: 1 held back, 1 correct.
- **Oct 21 evening: departures 30+ days out only** (user's call; `scan_config.json` `min_days_out` 3 → 30). Close-in
  dates leave the email, site and watchlist; Flight Search is unaffected. Replay: 42% of emails Oct 6–21 left within 30
  days, including 3 of the 5 that fell. Watch Oct 22: no published card under 30 days out, scan calls ~240 as before,
  email count (expect a dip), selection rounds.
- **Oct 22: clean, no code change.** GitHub schedule (13:47 UTC; 19 min). First run on the 30-day rule: earliest published
  card Nov 22, scan 227 calls, 3 emailed (down from 7), selection 8 rounds/55 lookups. All 3 above bar live the same day:
  JFK–ANU American 9.5k economy (held miles) 2.51¢ → 2.61¢ vs 2.24; JFK–LHR Alaska business 45k 4.15¢ → 3.70¢ vs 2.71;
  JFK–MAD Alaska business 55k 3.50¢ → 3.37¢ vs 2.71. Google healthy: one-way 239/244, round trips 617/629 (98%). 73 flight
  lookups. 1 repeat by the rule: JFK–LHR Alaska business 45k, emailed Oct 21 at 3.47¢ (Oct 27 date) and again today at
  4.15¢ (Dec 27, 1.19×): same points, a higher fare on another date. Third day running on this route (55k Oct 20). Like
  JFK–ZRH Oct 12, the "better" CPP came from the fare, not the points. If it keeps happening, consider judging repeats at the
  usual fare (`cpp_at_typical`) instead of today's; not proposed yet. Usual-fare rule held back nothing new (JFK–FRA 1.89¢
  was under the margin anyway).
- **Oct 23: clean, no code change.** GitHub schedule (13:25 UTC; 17 min). 1 emailed, the fewest yet: EWR–BKK United
  business 110k (watchlist Thailand, Feb 9) 2.54¢ → 2.63¢ live vs 2.10. The other 28 cards: ~16 already emailed within
  14 days and not materially better, 12 slow, 1 under bar at the usual fare (JFK–FRA). Expected with the 30-day rule: the
  pool is the same far-out deals day to day. Google: one-way 242/248, round trips 687/687 (100%). 85 flight lookups (just
  over the ~80 norm). Selection 12 rounds/62 lookups, no warning (nothing left unchecked); rounds Oct 20–23: 11, 7, 8, 12.
  If it hits 12 with the warning again, investigate with a reproducing test.
- **Oct 23 evening: "Still available" email section** (user's call). Up to 3 cards emailed 7+ days ago, still listed at
  ≥1.5× their bar, are shown again at the end of the email, each at most once a week, only alongside new deals. Replay
  Oct 15–23: 6 shown in 9 emails. Watch Oct 24: `make audit` `still available:` line and `AGAIN` rows (expect 1–3:
  EWR–JNB United premium economy, then JFK–CMN / EWR–ATH / JFK–PTY from Oct 25–26), and `make audit-live` covers them.
- **Oct 23 evening: test-coverage audit** (user's request; 4 subagents). 200 → 486 tests, line coverage 87% → 97%
  (`tools/audit_digest.py` 29% → 99%, `route_search.py` 53% → 100%, `deal_finder.py` 87% → 98%). The Streamlit pages in
  `views/` stay at 47–83% (UI, covered by the AppTest smoke test). Bugs it found, all fixed with a test that fails without the fix:
  the stop when every Google lookup fails could never fire after a failed exploration lookup (a fully blocked day would make
  ~190 failing lookups, not 5); `make audit` crashed on a digest with no `scan` block; the audit's `usual fare: … not emailed`
  list included emailed cards; site cards named the wrong airline on the same-carrier fare line and dropped the year on
  other dates in another year; `seats_aero.search_award_availability` crashed on a null seat count; `program_balances.json`
  wasn't written atomically. One existing ledger test passed without reaching the check it was named for (repaired).
- **Oct 24: clean, no code change.** GitHub schedule (12:40 UTC; 18 min). 2 emailed, both above bar live Oct 25: EWR–JNB
  United business 80k (Nov 27) 5.16¢ → 4.81¢ vs 2.10; EWR–GRU United PE 55k 2.26¢ (unchanged) vs 1.80. 1 repeat by the rule:
  EWR–JNB business 3.74¢ (Oct 21) → 5.16¢, same 80k points on a pricier date (third fare-driven repeat after JFK–ZRH, JFK–LHR).
  Google: one-way 252/257, round trips 747/747 (100%). 84 flight lookups. Selection hit 12 rounds with the warning (1 unchecked,
  checked by the safety net), 60 lookups. No "Still available" cards: none qualified (EWR–JNB PE fell to 1.47× its bar; the
  ≥1.5× ones were emailed 3–4 days earlier).
- **Oct 25: clean; logging added.** GitHub schedule (13:11 UTC; 20 min). 1 emailed: JFK–PLS Alaska business 15k (watchlist
  Caribbean, Jan 2) 3.04¢ vs 2.71, unchanged live. Google: one-way 238/244 (8 of 14 on the retry), round trips 630/708 (89%).
  83 flight lookups. Selection hit 12 rounds with the warning again (2 unchecked, checked by the safety net), 54 lookups.
  Rounds Oct 20–25: 11, 7, 8, 12, 12, 12, up since the 30-day rule (Oct 21). Lookups stay in the normal range, so this is
  slow convergence, not quota churn, but the log gave only totals. Added a `Selection rounds:` line, printed when a
  selection takes 8+ rounds, listing per round each deal checked and its fate (kept / gone / below bar / replaced, and
  whether it connects) and what moved in. Logging only, so not a streak reset. **Read that line on Oct 26** and write the
  reproducing test from what it shows. Still available: none qualified again; EWR–ATH, JFK–EZE PE and JFK–LHR Virgin PE
  become eligible Oct 27–28.
- **Oct 26–Nov 7: audited together on Nov 7; no code change in that span.** A digest every day (GitHub schedule,
  or the Claude routine on Nov 2–5; cron-job.org still never fired). 37 emailed. Offline audit: no emailed card with
  a problem except Oct 29's "Still available" EWR–TLV on a 3-day-old fare. Live: Nov 6's 3 and Nov 7's 3 all above
  bar (thinnest JFK–CPH Flying Blue 45k, 2.80¢ → 2.42¢ vs 2.27); earlier days weren't live-checked within a day.
  THROTTLED Oct 28 (58%) and Nov 7 (55%; 10 of 23 site cards on an old fare, none emailed). Selection hit 12 rounds
  Nov 2, 4, 5 without the warning; lookups normal (45–65). Flight lookups 110 on Nov 6 (cap 260).
  **Re-sends after the 14-day memory:** 18 of the 37 emailed cards repeated a trip emailed 14–26 days earlier, none
  materially better (e.g. JFK–EZE Alaska PE 32.5k 4.09¢ Oct 19 → shown "Still available" Oct 27 → "new" Nov 2;
  EWR–ATH Emirates 5.51¢ Oct 20 → 3.31¢ "new" Nov 3). By the letter (14-day rule, `--repeats`) these aren't repeats;
  with the 30-day lead time the pool is the same far-out deals, so it re-emails them every ~2 weeks. Proposed to the
  user, shipped the same day at their request: `ROUTE_MEMORY_DAYS` 60 (see decisions.md); history refilled from
  git; `--repeats` now reads 60 days. Replay: 37 → 19 emailed, re-sent trips 20 → 2. A code change, so the
  clean-day count restarts (0 of 5). **Watch Nov 8:** `--repeats` shows 0 unimproved repeats; expect fewer
  emails and some "Still available" cards; a day with nothing new sends no email (that's correct).
  Transfer-bonus PRs #2–#4 unmerged (the digest warns "last checked 26 days ago"); #4 has Chase 30% to
  Iberia/BA/Aer Lingus through Dec 1.
- **Nov 8–14: audited together on Nov 14; one code fix.** A digest every day (GitHub schedule Nov 8–9 and 14,
  the Claude routine Nov 10–13). 12 emailed, 0 unimproved repeats (`--repeats`: every re-send was materially
  better); the 60-day memory works. Live (Nov 14): all emailed cards from Nov 12–14 still above bar, none moved down
  (JFK–EZE Alaska business 50k 3.40¢ → 4.37¢, EWR–DEL United 88k 3.32¢ → 4.10¢). But by the letter not clean:
  THROTTLED Nov 10, 11, 13, 14 (round trips 57–61% on 11/13/14), and **published cards on an earlier day's fare
  climbed to 5, 8, 10 of ~28** (Nov 11/13/14), 4 of them emailed (`OLD FARE`: JFK–CMN Qatar, JFK–LHR Virgin PE
  on an Oct 16 fare, JFK–DUB, JFK–EZE on Nov 1's), plus EWR–DEL Nov 12 emailed on an estimated round trip. Every one
  held live (most went *up*), so holding such cards back would have cost 4 good emails; not done. Cause: the
  fresh re-pricing asked Google once, and on an empty answer kept the saved fare. Fix: empties are asked once
  more at the end, 2 s apart, with `refresh` (`FRESH_RETRY_MAX` 40). Log line now ends `(N asked twice)`. **Watch
  Nov 15:** `make audit` `fares:` back toward ~0–3 on a throttled day; run time (+≤80 s). Clean-day count: 0 of 5.
  Also seen, not fixed: flight lookups 99–129 a day (86 Nov 8; norm ~50–80, cap 260). Selection hit 12 rounds with
  the warning Nov 13 and 14 (54–55 lookups); the `Selection rounds:` line shows the tail: from round ~5, one card per
  round, each Aeroplan/United connecting route (EWR–CAI 8 times, then EWR–CUZ, –PMO, –CTA) "replaced (connects)"
  by a sibling date on the same route that is assumed nonstop until checked. That is the reproducing test to
  write next: a route already seen connecting should mark its unchecked siblings as connecting.
- **Nov 15–16: audited Nov 16; one code fix.** Nov 15 ran on GitHub's schedule (15:39 UTC, before the Nov 15
  fixes were pushed); Nov 16 on the schedule at 18:31 UTC. The Claude routine fired at 16:06 Nov 16 but failed
  with `USAGE_LIMIT_REACHED` (the user's plan limit), so it did not request a run. Nov 16 (first run with the
  Nov 15 fixes): 1 emailed, JFK–CDG Virgin Atlantic premium economy 24k+$162, 3.48¢ → 3.47¢ live (bar 2.10);
  0 repeats; no `OLD FARE` or estimated-round-trip emails (Nov 15 emailed EWR–CAI United on both). `fares:` 14 →
  9 → 4 of ~28 (Nov 14/15/16): falling, not ~0; the 4 match the 4 "unavailable" in fresh re-pricing. Still
  THROTTLED (round trips 61–62% both days). Flight lookups 94 → 89. Main selection settled in 9 rounds; **held
  miles hit 12 rounds with the warning** (32 lookups). Cause, half of it: **every American check was "gone"
  since ~Nov 6** — the scan now reports American seats, so its trip lookup's 0 (on every itinerary) was read as
  sold out (see decisions.md). Fixed with `seats_known`. The other half, JetBlue JFK/EWR–KEF economy going
  "below bar" on 12 checked dates in a row, is not understood: the round notes now print the value before →
  after the check, the bar, fare change and stops. **Watch Nov 17:** American cards back (held miles /
  Caribbean); held-miles selection under 12 rounds; if KEF cascades again, read the new `below bar (…)` detail.
  Clean-day count: 0 of 5.
- **Google empties on the runner (investigated Oct 15–16).** Every daily run since Oct 10 lost 17–55% of
  round trips (Sep 28–Oct 9: 98–100%, except Oct 4). Not our code (the drop predates the Oct 11 changes) and
  not a block page: the empties are Google's ordinary "no flights" answer, sometimes false (CPT priced from the
  Mac, "no flights" on the runner). From the Mac, 37 of 40 of Oct 15's empties priced. `tools/google_probe.py`
  (workflow "Google probe") re-runs the latest empties on a runner three ways: fresh client, warmed session,
  session+gl=us. Oct 16 01:12 UTC: 77–83% priced, all three alike; unpaced 73–80%, so not pacing or request
  style. Leading suspect: time of day (daily runs 13:25–16:07 UTC). The probe runs at 01:40/07:40/13:40/19:40
  UTC to test it — compare `Summary` lines by start time. Oct 16–17 probes (GitHub starts them late): 06:09–06:20 UTC
  30–83% (worst), 12:41/17:41/22:11/11:44 UTC 80–90%. So far nights are *not* cleaner; time of day is weakening as
  the suspect. Oct 17–18 probes: 16:39 57–77%, 21:42 63–67%, 06:34 43–63% (worst again), 12:10 63–90%; yet the
  Oct 18 daily run at 12:42 priced ~100%. The probe re-asks the previous run's hardest empties, so it reads low. Give it a few more days before deciding. If nights are clean, the fix is moving the run
  (the email would arrive in the evening Eastern; ask the user). Options if not: self-hosted runner on the Mac,
  residential proxy (fast-flights `proxy=`), paid API (last resort; the user removed SerpApi).
  **Probe schedule stopped Nov 15 2026: inconclusive on time of day; run by hand if needed.**
- **iCloud evicts (Oct 11; the repo has since moved to `~/Developer`, outside iCloud — keep for if it moves back) this repo's files** (`ls -lO` shows `dataless`; the `.nosync` name doesn't stop it).
  Reads then hang forever: git fetch/pull and `.venv` Python stalled on Oct 11. Check with
  `find .venv -type f | head -500 | xargs ls -lO | grep -c dataless`. A venv outside `~/Documents`
  works around it; the real fix is the user's (keep the folder downloaded / move it out of iCloud).
