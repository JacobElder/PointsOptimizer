"""Fixes from the Nov 15 audit of deal_finder (selection churn, fare reconciliation after
flight checks, swapped-in cards, email rules for estimated cards, repeats at the usual
fare, "Still available" freshness, and the `emailed` flag)."""

from datetime import datetime, timezone

import pytest

import award_scanner
import award_trips
import cash_quotes
import deal_finder
import fare_model


def _scored(dest, source, cash, date, aid, points=75000, origin="EWR", cabin="BUSINESS"):
    c = award_scanner.AwardCandidate(
        id=aid, source=source, program=source.title(), origin=origin, dest=dest, date=date,
        cabin=cabin, points=points, taxes=50.0, taxes_currency="USD", seats=2, direct=True,
        airlines="", distance=5600, updated_at="")
    s = deal_finder.Scored(c, 50.0, fare_model.Estimate(cash, 0.3, 0, "model"), 2.0, 0.0, 0.5)
    deal_finder._apply_quote(s, cash_quotes.Quote(origin, dest, cabin, date, cash, None, "t", ""))
    return s


def _trip_info(points, **over):
    kw = dict(flights=["X1 EWR–FRA", "X2 FRA–CAI"], connections=["FRA"], duration_min=600,
              departs_at="", arrives_at="", leg_cabins=["business"] * 2, mixed_cabin=False,
              lower_cabin_legs=[], carriers="", booking_url=None, booking_label=None,
              other_itineraries=0, airport_changes=[], stops=1, nonstop=False, seats=2,
              current_points=points, price_matches=True)
    kw.update(over)
    return award_trips.TripInfo(**kw)


# ── 1. a card past its check cap is not explored further ─────────────────────
@pytest.mark.parametrize("flaw", [
    {"mixed_cabin": True, "lower_cabin_legs": ["economy"], "leg_cabins": ["business", "economy"]},
    {"airport_changes": ["LGA"]},
    {"duration_min": 1500},  # slow, and a connection
], ids=["mixed-cabin", "airport-change", "slow-connection"])
def test_a_card_past_its_check_cap_is_not_explored_further(monkeypatch, flaw):
    """Nov 13-14: selection used all 12 rounds, one card per round from round ~5, each an
    EWR-CAI date "replaced (connects)" by a sibling date. Past the per-card cap an
    unchecked option only took a 0.6 discount, which a checked mixed-cabin (0.4) or
    airport-change (0.5) option still lost to, so every round checked one more date and
    route_key includes the program, so it cycled Aeroplan -> United -> LifeMiles."""
    lookups = []

    def fetch(aid, cabin, points):
        lookups.append(aid)
        return _trip_info(points, **(flaw if aid.startswith("CAI") else {}))

    monkeypatch.setattr(deal_finder.award_trips, "fetch", fetch)
    scored = []
    for i in range(10):
        for src in ("aeroplan", "united", "lifemiles"):
            scored.append(_scored("CAI", src, 3400, f"2027-0{1 + i % 9}-{10 + i:02d}", f"CAI:{src}:{i}"))
    for d in ("ZRH", "FRA", "LIS", "ATH"):
        scored.append(_scored(d, "aeroplan", 3300, "2027-03-01", f"{d}:x"))
    msgs = []
    published = deal_finder.verify_selection(scored, lambda ls: ls[:5], None, {}, set(),
                                             round_trip=False, log=msgs.append)
    cai = [a for a in lookups if a.startswith("CAI")]
    assert len(cai) <= 2, cai
    assert not any("didn't settle" in m for m in msgs), msgs
    assert {s.c.dest for s in published} == {"CAI", "ZRH", "FRA", "LIS", "ATH"}
    assert all(s.trip is not None for s in published)


# ── 2. a flight check doesn't undo fare reconciliation ───────────────────────
def _now_ts(hours_ago=0.0):
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ist(cash, date, aid, offers=None, fetched_at=""):
    s = _scored("IST", "turkish", cash, date, aid, points=65000)
    q = cash_quotes.Quote("EWR", "IST", "BUSINESS", date, cash, None, "t", fetched_at, offers=offers)
    deal_finder._apply_quote(s, q)
    return s


def test_a_flight_check_keeps_the_cheaper_nearby_fare(monkeypatch):
    """EWR-IST business a day apart at $3,536 and $2,006 (the reconcile_fares example).
    Reconciled before selection to $2,006, then the flight check re-applied the date's
    own quote and reset it to $3,536, so the dearer date led the card again."""
    dear = _ist(3536, "2027-03-01", "a")
    cheap = _ist(2006, "2027-03-02", "b")
    deal_finder.reconcile_fares([dear, cheap], log=lambda *a: None)
    assert dear.cash == 2006
    monkeypatch.setattr(deal_finder.award_trips, "fetch", lambda aid, cabin, points: _trip_info(points))
    deal_finder.attach_trips([dear], {})
    assert dear.trip is not None and not dear.trip.nonstop
    assert dear.cash == 2006 and dear.fare_reconciled


def test_a_confirmed_nonstop_drops_a_pooled_any_stops_fare(monkeypatch):
    """Before checks every award is compared with the cheapest fare at any stops, so
    that is what was pooled. A confirmed nonstop is compared with <=1-stop fares: it
    mustn't keep a connecting-grade fare from another date."""
    dear = _ist(3536, "2027-03-01", "a", offers=[[3536, 0, "TK"], [3000, 2, "XX"]])
    cheap = _ist(2006, "2027-03-02", "b", offers=[[2006, 2, "XX"], [2600, 1, "TK"]])
    deal_finder.reconcile_fares([dear, cheap], log=lambda *a: None)
    assert dear.cash == 2006
    monkeypatch.setattr(deal_finder.award_trips, "fetch",
                        lambda aid, cabin, points: _trip_info(points, flights=["TK12 EWR–IST"],
                                                              connections=[], stops=0, nonstop=True))
    deal_finder.attach_trips([dear], {})
    assert dear.nonstop is True
    assert dear.cash == 3536 and not dear.fare_reconciled
    # ...and reconciling again doesn't pool it with the unchecked date's any-stops fare.
    deal_finder.reconcile_fares([dear, cheap], log=lambda *a: None)
    assert dear.cash == 3536


def test_published_cards_pool_only_with_fares_fetched_this_run():
    """After fresh re-pricing the shortlist is pooled again, with this run's fares for
    nearby dates too (not only other shortlisted cards), but never with a saved fare
    from an earlier day."""
    card = _ist(3536, "2027-03-01", "a", fetched_at=_now_ts(0.1))
    fresh_neighbor = _ist(2006, "2027-03-02", "b", fetched_at=_now_ts(0.5))
    old_neighbor = _ist(1800, "2027-03-03", "c", fetched_at=_now_ts(72))
    n = deal_finder.reconcile_published([card], [card, fresh_neighbor, old_neighbor],
                                        log=lambda *a: None)
    assert n == 1
    assert card.cash == 2006 and card.fare_reconciled
    assert old_neighbor.cash == 1800 and fresh_neighbor.cash == 2006  # only cards are revalued


def test_fresh_re_pricing_replaces_a_pooled_fare(monkeypatch):
    """The fresh exact-date fare replaces whatever was pooled before selection (which
    may come from an earlier day); reconcile_published then pools fresh fares only."""
    card = _ist(3536, "2027-03-01", "a")
    deal_finder.reconcile_fares([card, _ist(1800, "2027-03-03", "c")], log=lambda *a: None)
    assert card.cash == 1800
    monkeypatch.setattr(cash_quotes, "get_quote", lambda *a, **k: cash_quotes.Quote(
        "EWR", "IST", "BUSINESS", "2027-03-01", 3400.0, None, "t", _now_ts()))
    deal_finder.reprice_fresh([card], log=lambda *a: None)
    assert card.cash == 3400 and not card.fare_reconciled


# ── 3. swapped-in alternatives are re-priced fresh ────────────────────────────
def _edi(monkeypatch):
    import dataclasses
    import funding
    from test_deal_finder import _trip
    monkeypatch.setattr(funding, "active_bonus", lambda *a, **k: None)
    # Chase-only balances: pooled ties now go to a pool with points (Nov 15), and this
    # scenario is "45k Alaska vs 115k United paid from Chase".
    monkeypatch.setattr(funding, "_funded_cache", {})
    monkeypatch.setattr(funding.ledger, "load_balances", lambda: {"chase_ur": 110000})
    funding._cheapest_transfer.cache_clear()

    def real(s, program):
        s.c = dataclasses.replace(s.c, program=program)
        deal_finder._finalize_cash(s)
        s.trip = _trip()
        return s

    united = real(_scored("EDI", "united", 3100, "2027-02-05", "u", points=115000, origin="JFK"),
                  "United MileagePlus")
    # Alaska's $1,426 is a saved quote from an earlier day.
    alaska = real(_scored("EDI", "alaska", 1426, "2027-02-19", "a", points=45000, origin="JFK"),
                  "Alaska Atmos Rewards")
    (leader,) = deal_finder.group_leaders([united, alaska])
    assert leader is united and leader.alt_awards == [alaska]
    united.one_way_cash = 2887.0  # fresh re-pricing lowered United's fare after the choice
    deal_finder._finalize_cash(united)
    return united, alaska


def test_a_swapped_in_alternative_is_repriced_before_it_replaces_the_leader(monkeypatch):
    """promote_better_alternatives ran after fresh re-pricing, so a swapped-in card was
    published on whatever fare it had, often an earlier day's."""
    united, alaska = _edi(monkeypatch)
    asked = []

    def quote(origin, dest, date, cabin, **k):
        asked.append((origin, dest, date))
        return cash_quotes.Quote(origin, dest, cabin, date, 700.0, None, "t", _now_ts())

    monkeypatch.setattr(cash_quotes, "get_quote", quote)
    swaps = deal_finder.promote_better_alternatives([united], [united, alaska], None, None, set(),
                                                    False, fresh=True)
    assert asked == [("JFK", "EDI", "2027-02-19")]
    # At today's $700 Alaska is under its bar: United stays.
    assert alaska.cash == 700 and swaps == {}


def test_a_swapped_in_alternative_publishes_on_todays_fare(monkeypatch):
    united, alaska = _edi(monkeypatch)
    monkeypatch.setattr(cash_quotes, "get_quote", lambda origin, dest, date, cabin, **k:
                        cash_quotes.Quote(origin, dest, cabin, date, 1500.0, None, "t", _now_ts()))
    swaps = deal_finder.promote_better_alternatives([united], [united, alaska], None, None, set(),
                                                    False, fresh=True)
    assert swaps == {id(united): alaska}
    assert alaska.cash == 1500 and deal_finder._fetched_this_run(alaska)


# ── 5. estimated round trips and unknown taxes stay on the site ──────────────
def _card(**over):
    d = {"source": "united", "origin": "EWR", "dest": "DEL", "cabin": "BUSINESS", "date": "2027-01-10",
         "points": 88000, "cpp": 3.32, "great_floor": 2.10, "slow": False, "cpp_at_typical": None,
         "rt_unavailable": False, "taxes_unknown": False}
    d.update(over)
    return d


def test_a_card_on_an_estimated_round_trip_is_not_emailed():
    """Nov 12: EWR-DEL United 88k was emailed valued at 81% of the one-way fare because
    no round trip could be priced (user's call, Nov 15: those stay on the site)."""
    assert deal_finder.worth_emailing(_card())
    assert not deal_finder.worth_emailing(_card(rt_unavailable=True))


def test_a_card_with_unknown_taxes_is_not_emailed():
    assert not deal_finder.worth_emailing(_card(taxes_unknown=True))


# ── 6. repeats are judged at the usual fare ──────────────────────────────────
def test_same_points_at_a_higher_fare_is_not_a_better_deal():
    """JFK-EZE Alaska business 60,000: emailed Nov 6 at 3.52c, again Nov 9 at 4.25c on a
    pricier date. At the route's usual fare the Nov 9 card was 3.19c: no better."""
    prev = {"at": "2026-11-06T13:00:00+00:00", "cpp": 3.517, "points": 60000}
    eze = _card(source="alaska", origin="JFK", dest="EZE", points=60000, cpp=4.254,
                cpp_at_typical=3.185, great_floor=2.71)
    assert not deal_finder.materially_better(eze, prev)
    # Better at the usual fare too: still a new deal.
    assert deal_finder.materially_better({**eze, "cpp_at_typical": 4.10}, prev)
    # No usual fare known: today's fare decides, as before.
    assert deal_finder.materially_better({**eze, "cpp_at_typical": None}, prev)
    assert deal_finder.materially_better({**eze, "points": 50000}, prev)  # fewer points


def test_route_memory_keeps_the_value_at_the_usual_fare():
    eze = _card(source="alaska", origin="JFK", dest="EZE", points=60000, cpp=4.254,
                cpp_at_typical=3.185)
    entry = deal_finder.route_memory_entry(eze, "2026-11-09T13:00:00+00:00")
    assert entry == {"at": "2026-11-09T13:00:00+00:00", "cpp": 4.254, "points": 60000,
                     "cpp_at_usual": 3.185}
    # The next card is judged against 3.185, not 4.254.
    assert deal_finder.materially_better({**eze, "cpp": 3.70, "cpp_at_typical": 3.70}, entry)


# ── 7. "Still available" only on a fare fetched today, a week by UTC date ─────
def _again(**over):
    return {**_card(source="united", origin="EWR", dest="TLV", cpp=4.0, great_floor=2.10,
                    new=False), "fare_this_run": True, **over}


def test_still_available_needs_a_fare_fetched_in_this_run():
    """The section says "re-checked today", but 4 of 6 such cards Nov 11-14 were valued on
    a fare borrowed from a nearby date and fetched days earlier (EWR-TLV Nov 14 on a
    Nov 8 fare)."""
    now = datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)
    routes = {"united|EWR|TLV|BUSINESS": {"at": "2026-11-01T13:00:00+00:00", "cpp": 3.0,
                                          "points": 88000}}
    pairs = [("k", _again())]
    assert deal_finder.pick_still_available(pairs, routes, {}, now) == [pairs[0][1]]
    assert deal_finder.pick_still_available([("k", _again(fare_this_run=False))], routes, {}, now) == []
    assert deal_finder.pick_still_available([("k", _again(fare_this_run=None))], routes, {}, now) == []


def test_still_available_waits_seven_calendar_days_not_168_hours():
    """Runs start 12-16 UTC, so a deal emailed at 16:00 and checked a week later at 12:00
    was 6.8 days old by the clock and waited until day 8."""
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    routes = {"united|EWR|TLV|BUSINESS": {"at": "2026-11-07T16:00:00+00:00", "cpp": 3.0,
                                          "points": 88000}}
    assert len(deal_finder.pick_still_available([("k", _again())], routes, {}, now)) == 1
    routes["united|EWR|TLV|BUSINESS"]["at"] = "2026-11-08T01:00:00+00:00"  # 6 dates ago
    assert deal_finder.pick_still_available([("k", _again())], routes, {}, now) == []


def test_a_card_says_whether_its_fare_was_fetched_in_this_run():
    s = _ist(3536, "2027-03-01", "a", fetched_at=_now_ts(0.1))
    assert s.to_dict()["fare_this_run"] is True
    assert _ist(3536, "2027-03-01", "a", fetched_at=_now_ts(30)).to_dict()["fare_this_run"] is False


# ── 9. no round trip means no round-trip half ────────────────────────────────
class _RT:
    def __init__(self, price, stops=1):
        self.price_usd, self.stops = price, stops


def _pvg():
    """Oct 29: EWR-PVG business (Apr 26) published with rt_unavailable=True and a 4-night
    round_trip_half of $3,875 on a $3,833 one-way fare."""
    return _scored("PVG", "united", 3833, "2027-04-26", "pvg", points=88000)


def test_a_round_trip_priced_on_a_shorter_stay_is_not_thrown_away():
    """The end-of-run retry priced the 4-night return; a later apply_round_trip looked
    only at the (empty) 7-night one and marked the deal as having no round trip."""
    from datetime import date
    s = _pvg()
    cache = {deal_finder._rt_key(s, 7): [], deal_finder._rt_key(s, 4): [_RT(7750.0)]}
    assert deal_finder.apply_round_trip(s, cache, today=date(2026, 10, 29))
    assert s.round_trip_half == 3875 and s.rt_stay_nights == 4 and not s.rt_unavailable
    assert s.cash == 3833


def test_marking_no_round_trip_clears_a_stale_half():
    s = _pvg()
    s.round_trip_half, s.rt_stay_nights = 3875.0, 4
    deal_finder._mark_rt_unavailable(s)
    assert s.rt_unavailable and s.round_trip_half is None
    assert s.cash == pytest.approx(3833 * 0.81)


# ── 10. a fresh quote that matched no comparable fare isn't "re-checked" ─────
def test_a_fresh_quote_with_no_comparable_fare_is_not_counted_as_repriced(monkeypatch):
    s = _ist(3536, "2027-03-01", "a")
    monkeypatch.setattr(cash_quotes, "get_quote", lambda *a, **k: cash_quotes.Quote(
        "EWR", "IST", "BUSINESS", "2027-03-01", 3000.0, None, "t", _now_ts()))
    monkeypatch.setattr(cash_quotes, "comparable_fare", lambda *a, **k: None)
    monkeypatch.setattr(deal_finder, "FRESH_RETRY_PAUSE_S", 0)
    stats = deal_finder.reprice_fresh([s], log=lambda *a: None)
    assert stats["repriced"] == 0 and stats["failed"] == 1
    assert s.cash == 3536


# ── Nov 19-22: one route's round trips priced one date at a time ─────────────
def test_a_route_whose_round_trips_fail_the_bar_stops_promoting_its_other_dates(monkeypatch):
    """Nov 19-22 the held-miles selection used all 12 rounds: JFK/EWR-KEF JetBlue economy
    is $407 one-way (Icelandair nonstop) on nearly every date, 2.40c against a 2.08c
    bar, but half the round trip is $190 (0.69c). Each date stayed ranked on its
    one-way fare until its own round trip was priced, so the free round-trip stage
    priced one date per pass, ran out of passes, and the next date cost a seats.aero
    lookup -- 12 of them, and 0 held-miles deals published on Nov 21 and 22."""
    import types
    rt_lookups, trip_lookups = [], []

    def rt(origin, dest, depart, ret, cabin):
        rt_lookups.append((origin, dest, depart))
        price = 380.0 if dest == "KEF" else 900.0  # KEF: half is $190; GCM: half above one-way
        return [types.SimpleNamespace(price_usd=price, stops=0)]

    def fetch(aid, cabin, points):
        trip_lookups.append(aid)
        return _trip_info(points, flights=["B6 1"], connections=[], stops=0, nonstop=True,
                          leg_cabins=["economy"])

    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers", rt)
    monkeypatch.setattr(deal_finder.award_trips, "fetch", fetch)
    scored = []
    for i in range(40):
        origin = "JFK" if i % 2 else "EWR"
        date = f"2027-0{1 + i // 10}-{10 + i % 10:02d}"
        s = _scored("KEF", "jetblue", 407.0, date, f"KEF:{i}", points=12500, origin=origin,
                    cabin="ECONOMY")
        s.taxes_usd = 107.0
        deal_finder._set_cash(s, 407.0)
        scored.append(s)
    gcm = _scored("GCM", "american", 450.0, "2027-02-21", "GCM:1", points=15000, origin="JFK",
                  cabin="ECONOMY")
    scored.append(gcm)
    msgs = []
    published = deal_finder.verify_selection(
        scored, lambda ls: sorted(ls, key=lambda s: -s.rank_value)[:4], rt_cache={},
        trip_cache={}, sources_reporting_seats=set(), round_trip=True,
        bar_fn=lambda s: 2.08, log=msgs.append)
    assert not any("didn't settle" in m for m in msgs), msgs
    kef_trips = [a for a in trip_lookups if a.startswith("KEF")]
    assert len(kef_trips) == 0, kef_trips  # every KEF date is below bar on its round trip
    kef_rt = [k for k in rt_lookups if k[1] == "KEF"]
    assert len(kef_rt) <= 4, len(kef_rt)  # a couple per origin, not one per date
    assert [s.c.dest for s in published] == ["GCM"]
    # Held-back awards are named, so a real deal hidden by the rule shows in the log.
    held = [m for m in msgs if m.startswith("Held back")]
    assert held and "JFK-KEF E jetblue" in held[0] and "EWR-KEF E jetblue" in held[0], msgs
