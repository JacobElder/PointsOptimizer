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
