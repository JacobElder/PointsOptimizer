"""deal_finder behaviors the main test files didn't reach: the cash-pricing paths
(cache, no-fare, throttling stop, retries), the warnings a run prints, run()'s failure
branches (empty scan, quota, email failure) and the CLI's exit codes.

No network: every provider is monkeypatched (see conftest.py and the fakes in
test_run_end_to_end.py).
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import award_scanner
import award_trips
import cash_quotes
import deal_finder
import fare_model
import flight_search
import seats_aero
from test_deal_finder import _RT, _scored, _trip
from test_run_end_to_end import _award, _fake_pipeline


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(days_out: int) -> str:
    return (date.today() + timedelta(days=days_out)).isoformat()


def _unpriced(dest="CMN", source="united", origin="EWR", points=88000, days_out=60, p_great=0.9):
    s = _scored(dest, "BUSINESS", source, origin, points, 1.0, date=_day(days_out))
    s.cash = s.cpp = s.surplus = s.one_way_cash = None
    s.quote = None
    s.p_great = p_great
    return s


def _row(s, price, hours_ago):
    """A saved quote row for this award's own flight date."""
    return {"origin": s.c.origin, "dest": s.c.dest, "cabin": "BUSINESS", "date": s.c.date,
            "price_usd": price, "nonstop_price_usd": None, "provider": "t", "fetched_at": _ts(hours_ago)}


def _state(quotes=None):
    return {"quotes": list(quotes or []), "stop": False, "empty": {}}


def _stats():
    return {"cached": 0, "live": 0, "watch_live": 0, "explore_live": 0, "no_fare": 0, "failed": 0,
            "skipped_low_p": 0, "retried": 0, "recovered": 0, "estimate_errors": []}


def _quote_fn(price, asked=None):
    def _q(o, d, dt, cab, **k):
        if asked is not None:
            asked.append((d, k))
        return cash_quotes.Quote(o, d, cab, dt, price, None, "t", _ts(0))
    return _q


# ── _price_one: cache vs live vs no-fare ─────────────────────────────────────
def test_a_saved_fare_is_used_without_asking_google(monkeypatch):
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(2000.0, asked))
    s = _unpriced()
    stats = _stats()
    assert deal_finder._price_one(s, _state([_row(s, 3000.0, 2)]), stats, print) == "cached"
    assert asked == [] and s.cash == 3000.0 and stats["cached"] == 1 and stats["live"] == 0


def test_a_recent_no_fare_answer_is_not_asked_again_and_leaves_the_award_unpriced(monkeypatch):
    """A "no flights" answer from earlier in this run is trusted (no second live call)."""
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(2000.0, asked))
    s = _unpriced()
    stats = _stats()
    assert deal_finder._price_one(s, _state([_row(s, None, 6)]), stats, print) == "cached"
    assert asked == [] and s.cash is None and stats["cached"] == 0


def test_a_no_fare_answer_from_an_earlier_run_is_asked_again(monkeypatch):
    """Oct 10: empty answers saved as "no fare" blocked those dates from pricing for 2-7
    days. They are trusted only for 12 hours (NO_FARE_MAX_AGE_DAYS)."""
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(2000.0, asked))
    s = _unpriced()
    stats = _stats()
    assert deal_finder._price_one(s, _state([_row(s, None, 13)]), stats, print) == "live"
    assert len(asked) == 1 and s.cash == 2000.0 and stats["live"] == 1


def test_cache_only_pass_never_spends_a_live_lookup(monkeypatch):
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(2000.0, asked))
    s = _unpriced()
    assert deal_finder._price_one(s, _state(), _stats(), print, allow_live=False) == "skipped"
    assert asked == [] and s.cash is None


def test_unpromising_awards_are_skipped_unless_watched_or_bookable(monkeypatch):
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(2000.0, asked))
    stats = _stats()
    plain = _unpriced(p_great=0.1)
    assert deal_finder._price_one(plain, _state(), stats, print) == "skipped"
    assert stats["skipped_low_p"] == 1 and asked == []

    watched = _unpriced(dest="LIM", p_great=0.1)
    watched.watch, watched.p_watch = [deal_finder.WatchEntry(label="Peru", dests=["LIM"])], 0.06
    assert deal_finder._price_one(watched, _state(), stats, print) == "live"

    held = _unpriced(dest="NRT", p_great=0.06)
    held.held_miles = 88000  # the miles are already there: worth pricing at lower odds
    assert deal_finder._price_one(held, _state(), stats, print) == "live"
    assert [d for d, _ in asked] == ["LIM", "NRT"]


def test_a_date_outside_googles_window_is_skipped_without_counting_a_lookup(monkeypatch):
    def _out(*a, **k):
        raise cash_quotes.OutOfWindow("too far")
    monkeypatch.setattr(cash_quotes, "get_quote", _out)
    stats = _stats()
    assert deal_finder._price_one(_unpriced(), _state(), stats, print) == "skipped"
    assert stats["live"] == 0 and stats["failed"] == 0


def test_an_empty_live_answer_is_counted_and_remembered_for_the_retry(monkeypatch):
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(None))
    s = _unpriced()
    state, stats = _state(), _stats()
    assert deal_finder._price_one(s, state, stats, print) == "live"
    assert s.cash is None and stats["no_fare"] == 1
    assert state["empty"] == {(s.c.origin, s.c.dest, s.c.date, "BUSINESS"): True}
    assert len(state["quotes"]) == 1  # the empty answer is kept in the run's cache


def test_pricing_stops_once_five_lookups_in_a_row_have_all_failed(monkeypatch):
    """Google blocking the scraper must stop the run's pricing rather than burn the
    whole lookup limit on errors."""
    calls = []

    def _fail(*a, **k):
        calls.append(1)
        raise flight_search.SearchFailed("blocked")
    monkeypatch.setattr(cash_quotes, "get_quote", _fail)
    monkeypatch.setattr(deal_finder, "EXPLORE_SHARE", 0)
    logs = []
    scored = [_unpriced(dest=d) for d in ("CMN", "LIS", "NRT", "ICN", "HKG", "BKK", "SIN", "DEL")]
    stats = deal_finder.price_promising(scored, max_lookups=50, log=logs.append)
    assert len(calls) == 5 and stats["failed"] == 5
    assert stats["live"] == 5  # a failed lookup still counts against the per-run lookup limit
    assert any("stopping" in m for m in logs)
    assert stats["retried"] == 0  # a stopped run doesn't re-ask either


def test_pricing_stops_on_a_total_block_even_with_exploration_on(monkeypatch):
    """With the default 25% exploration share, a fully blocked Google should still stop
    pricing after 5 failed lookups, not spend the whole per-run lookup limit."""
    calls = []

    def _fail(*a, **k):
        calls.append(1)
        raise flight_search.SearchFailed("blocked")
    monkeypatch.setattr(cash_quotes, "get_quote", _fail)
    monkeypatch.setattr(cash_quotes, "load", lambda: [])
    scored = [_unpriced(dest=f"X{i:02d}") for i in range(60)]
    deal_finder.price_promising(scored, max_lookups=40, log=lambda m: None)
    assert len(calls) == 5


def test_failures_after_a_success_do_not_stop_pricing(monkeypatch):
    answers = iter([3000.0] + [None] * 10)

    def _q(o, d, dt, cab, **k):
        price = next(answers)
        if price is None:
            raise flight_search.SearchFailed("flaky")
        return cash_quotes.Quote(o, d, cab, dt, price, None, "t", _ts(0))
    monkeypatch.setattr(cash_quotes, "get_quote", _q)
    state, stats = _state(), _stats()
    for d in ("CMN", "LIS", "NRT", "ICN", "HKG", "BKK", "SIN"):
        deal_finder._price_one(_unpriced(dest=d), state, stats, print)
    assert stats["failed"] == 6 and state["stop"] is False


# ── _retry_empty_one_ways ────────────────────────────────────────────────────
def test_one_way_retries_are_spaced_out_and_survive_a_failed_retry(monkeypatch):
    sleeps = []
    monkeypatch.setattr(deal_finder.time, "sleep", sleeps.append)
    monkeypatch.setattr(deal_finder, "ONE_WAY_RETRY_PAUSE_S", 2.0)

    def _q(o, d, dt, cab, refresh=False, **k):
        if refresh and d == "CMN":
            raise flight_search.SearchFailed("still throttled")
        return cash_quotes.Quote(o, d, cab, dt, 2500.0, None, "t", _ts(0))
    monkeypatch.setattr(cash_quotes, "get_quote", _q)
    cmn, lis = _unpriced(dest="CMN"), _unpriced(dest="LIS")
    state = _state()
    state["empty"] = {deal_finder._quote_key(cmn): True, deal_finder._quote_key(lis): True}
    stats = _stats()
    logs = []
    deal_finder._retry_empty_one_ways([cmn, lis], state, stats, logs.append)
    assert sleeps == [2.0]  # between the two retries, not before the first
    assert stats["retried"] == 2 and stats["recovered"] == 1
    assert lis.cash == 2500.0 and cmn.cash is None
    assert logs == ["One-way retries: 1 of 2 empty lookups priced on a second try (2 came back empty)"]


def test_no_retry_when_nothing_came_back_empty(monkeypatch):
    monkeypatch.setattr(cash_quotes, "get_quote", lambda *a, **k: pytest.fail("asked Google"))
    logs = []
    deal_finder._retry_empty_one_ways([_unpriced()], _state(), _stats(), logs.append)
    assert logs == []


# ── reuse_holdout ────────────────────────────────────────────────────────────
def test_reuse_check_measures_how_far_off_a_borrowed_fare_was(monkeypatch):
    """Reused nearby-date fares are otherwise never compared with the real one."""
    a = _unpriced(dest="CMN", days_out=60)
    a2 = _unpriced(dest="CMN", source="aeroplan", days_out=60)  # same cell: measured once
    b = _unpriced(dest="LIS", days_out=60)
    no_nearby = _unpriced(dest="NRT", days_out=60)
    saved = []
    for s, price in ((a, 1000.0), (b, 2000.0)):
        row = _row(s, price, 2)
        row["date"] = _day(62)  # a nearby date, so the lookup for this date borrows it
        saved.append(row)
    monkeypatch.setattr(cash_quotes, "load", lambda: saved)
    fresh = {"CMN": 800.0, "LIS": 2000.0}
    asked = []

    def _q(o, d, dt, cab, allow_approx=True, **k):
        assert allow_approx is False  # must be this date's own fare
        asked.append(d)
        return cash_quotes.Quote(o, d, cab, dt, fresh[d], None, "t", _ts(0))
    monkeypatch.setattr(cash_quotes, "get_quote", _q)
    logs = []
    out = deal_finder.reuse_holdout([a, a2, b, no_nearby], budget=5, log=logs.append)
    assert asked == ["CMN", "LIS"]
    assert out == {"n": 2, "median_pct": 25.0, "max_pct": 25.0}  # |800-1000|/800, 0
    assert "off by 25.0% at the median" in logs[0]


def test_reuse_check_stops_at_its_budget_and_ignores_failed_lookups(monkeypatch):
    awards = [_unpriced(dest=d, days_out=60) for d in ("CMN", "LIS", "NRT", "ICN")]
    saved = []
    for s in awards:
        row = _row(s, 1000.0, 2)
        row["date"] = _day(61)
        saved.append(row)
    monkeypatch.setattr(cash_quotes, "load", lambda: saved)
    asked = []

    def _q(o, d, dt, cab, **k):
        asked.append(d)
        if d == "CMN":
            raise flight_search.SearchFailed("x")
        return cash_quotes.Quote(o, d, cab, dt, None if d == "LIS" else 1000.0, None, "t", _ts(0))
    monkeypatch.setattr(cash_quotes, "get_quote", _q)
    out = deal_finder.reuse_holdout(awards, budget=1, log=lambda m: None)
    assert asked == ["CMN", "LIS", "NRT"] and out["n"] == 1 and out["max_pct"] == 0.0


def test_reuse_check_reports_nothing_when_nothing_was_borrowed(monkeypatch):
    monkeypatch.setattr(cash_quotes, "load", lambda: [])
    monkeypatch.setattr(cash_quotes, "get_quote", lambda *a, **k: pytest.fail("asked Google"))
    assert deal_finder.reuse_holdout([_unpriced()], budget=5, log=lambda m: None) == {}


# ── _explore_pass ────────────────────────────────────────────────────────────
def test_exploration_stops_at_its_share_and_keeps_the_main_budget_intact(monkeypatch):
    asked = []
    monkeypatch.setattr(cash_quotes, "get_quote", _quote_fn(1500.0, asked))
    scored = []
    for d, sigma in (("NBO", 0.6), ("LOS", 0.5), ("ACC", 0.4)):
        s = _unpriced(dest=d, p_great=0.0)
        s.est = fare_model.Estimate(2000.0, sigma, 0, "model")
        scored.append(s)
    stats = _stats()
    logs = []
    spent = deal_finder._explore_pass(scored, 2, _state(), stats, logs.append)
    assert spent == 2 and asked and [d for d, _ in asked] == ["NBO", "LOS"]  # most uncertain first
    assert stats["live"] == 0  # the main pass's count isn't charged
    assert logs == ["Explored 2 route/cabin(s) with no quote in the last 10 days"]


# ── retry_round_trips ────────────────────────────────────────────────────────
def test_round_trip_retry_respects_its_limit_and_reuses_a_flight_already_priced(monkeypatch):
    calls = []

    def _rt(o, d, dep, ret, cab):
        calls.append(d)
        return [_RT(3000.0, 1)]
    monkeypatch.setattr(flight_search, "search_round_trip_offers", _rt)
    same_a = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(40))
    same_b = _scored("CPT", "BUSINESS", "aeroplan", "EWR", 90000, 5000, date=_day(40))  # same flight
    other = _scored("JNB", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(41))
    for s in (same_a, same_b, other):
        s.rt_unavailable = True
    sleeps = []
    monkeypatch.setattr(deal_finder.time, "sleep", sleeps.append)
    stats = deal_finder.retry_round_trips([same_a, same_b, other], {}, log=lambda m: None,
                                          max_lookups=1, pause_s=2.0)
    assert calls == ["CPT"] and sleeps == []
    assert stats == {"retried": 1, "recovered": 2}
    assert same_b.cash == 1500.0 and not same_b.rt_unavailable
    assert other.rt_unavailable and other.round_trip_half is None  # over the limit: still an estimate


def test_round_trip_retry_skips_deals_already_priced_on_a_round_trip(monkeypatch):
    monkeypatch.setattr(flight_search, "search_round_trip_offers", lambda *a: pytest.fail("asked"))
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(40))
    logs = []
    assert deal_finder.retry_round_trips([s], {}, log=logs.append) == {"retried": 0, "recovered": 0}
    assert logs == []


def test_a_failed_round_trip_search_is_an_estimate_not_a_crash(monkeypatch):
    def _boom(*a):
        raise flight_search.SearchFailed("x")
    monkeypatch.setattr(flight_search, "search_round_trip_offers", _boom)
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(40))
    assert deal_finder.apply_round_trip(s, {}) is False
    assert s.rt_unavailable and s.cash == pytest.approx(4050.0)  # 81% of the one-way, not 5000


def test_round_trip_needs_a_priced_award():
    s = _unpriced()
    assert deal_finder.apply_round_trip(s, {}) is False and not s.rt_unavailable


# ── warnings: fire when they should, stay quiet when they shouldn't ──────────
def test_source_drop_warning_stays_quiet_when_the_drop_is_explained():
    prev = {"scan": {"per_source": {"aeroplan": 5000, "united": 80, "flyingblue": 400}}}
    msgs = []
    # Quota ran out: every program looks like it dropped; the quota warning covers it.
    deal_finder._warn_on_source_drop({"quota_exhausted": True, "per_source": {}}, prev, msgs.append)
    # The program's slice failed, or the scan aborted: the failure warning covers it.
    deal_finder._warn_on_source_drop({"failed": ["aeroplan/BUSINESS"], "per_source": {"flyingblue": 400}},
                                     prev, msgs.append)
    deal_finder._warn_on_source_drop({"aborted": True, "per_source": {}}, prev, msgs.append)
    # Small programs (under 100 awards) swing a lot; a 50% dip isn't a drop either.
    deal_finder._warn_on_source_drop({"per_source": {"aeroplan": 2500, "united": 0, "flyingblue": 400}},
                                     prev, msgs.append)
    deal_finder._warn_on_source_drop({"per_source": {}}, {}, msgs.append)  # no previous digest
    assert msgs == []
    deal_finder._warn_on_source_drop({"per_source": {"aeroplan": 2499, "flyingblue": 400}}, prev, msgs.append)
    assert msgs == ["::warning::aeroplan returned 2,499 awards, down from 5,000 yesterday: "
                    "the program may have dropped out of seats.aero or lost availability."]


def test_stale_training_warning_needs_fewer_than_20_recent_fares():
    msgs = []
    recent, old = {"at": _ts(24)}, {"at": "2020-01-01T00:00:00Z"}
    deal_finder._warn_on_stale_training([recent] * 20 + [old] * 500, msgs.append)
    assert msgs == []
    deal_finder._warn_on_stale_training([recent] * 19 + [old] * 500, msgs.append)
    assert msgs and "Only 19 of 519 fares" in msgs[0]


def test_health_warnings_stay_quiet_on_a_normal_run():
    msgs = []
    # Oct 13: 25 of 261 empty; a full budget spent; 10% stale rows.
    deal_finder._warn_on_health({"live": 150, "watch_live": 80, "explore_live": 31, "no_fare": 25,
                                 "skipped_low_p": 5000}, {"rows": 1000, "stale": 100}, 150, msgs.append)
    # Exactly 30% empty is not over the line; no lookups at all can't divide by zero.
    deal_finder._warn_on_health({"live": 10, "no_fare": 3}, {}, 0, msgs.append)
    deal_finder._warn_on_health({}, {"rows": 0}, 150, msgs.append)
    assert msgs == []
    deal_finder._warn_on_health({"live": 10, "no_fare": 4}, {}, 0, msgs.append)
    assert msgs == ["::warning::4 of 10 cash lookups returned no fare: "
                    "Google may be blocking or serving empty pages."]


def test_throttle_note_at_its_thresholds():
    # 30% empty one-way and 70% of round trips priced are both still "fine".
    assert deal_finder.throttle_note({"one_way": 100, "one_way_empty": 30,
                                      "round_trips": 100, "round_trips_priced": 70}) is None
    note = deal_finder.throttle_note({"one_way": 100, "one_way_empty": 31,
                                      "round_trips": 100, "round_trips_priced": 70})
    assert note.startswith("Google Flights limited today's lookups: 31 of 100 one-way fares and "
                           "30 of 100 round trips came back empty.")
    assert deal_finder.throttle_note({"one_way": 100, "one_way_empty": 0,
                                      "round_trips": 100, "round_trips_priced": 69})


def test_rt_outage_note_at_its_threshold():
    assert deal_finder.rt_outage_note([]) is None
    three_of_ten = [{"rt_unavailable": True}] * 3 + [{"rt_unavailable": False}] * 7
    assert deal_finder.rt_outage_note(three_of_ten) is None  # 30% exactly is not an outage
    note = deal_finder.rt_outage_note([{"rt_unavailable": True}] * 4 + [{"rt_unavailable": False}] * 6)
    assert "4 of these 10" in note and "81% of the one-way" in note


def test_google_coverage_counts_what_is_still_empty_after_the_retry():
    cov = deal_finder.google_coverage({"live": 100, "watch_live": 50, "explore_live": 10,
                                       "no_fare": 40, "recovered": 25},
                                      {"a": [1], "b": None, "c": [], "d": [2]})
    assert cov == {"one_way": 160, "one_way_empty": 15, "one_way_recovered": 25,
                   "round_trips": 4, "round_trips_priced": 2}


# ── what a card says about itself ─────────────────────────────────────────────
def test_mixed_cabin_and_airport_change_are_explained_on_the_card():
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    s.trip = _trip(mixed_cabin=True, airport_changes=["LHR→LGW"], nonstop=False, stops=1)
    notes = s.rank_notes
    assert "part of the trip is in a lower cabin" in notes and "you'd change airports mid-trip" in notes


def test_unknown_taxes_rank_lower_than_estimated_taxes_lower_than_reported():
    reported = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    estimated = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    estimated.taxes_basis = "typical for United"
    unknown = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    unknown.taxes_usd = 0.0
    assert reported.quality_factor == 1.0
    assert estimated.quality_factor == pytest.approx(0.95)
    assert unknown.quality_factor == pytest.approx(0.85)
    assert "seats.aero reported no taxes, so the real cost may be higher" in unknown.rank_notes


def test_a_deal_below_its_baseline_is_not_boosted_by_discounts():
    s = _scored("CPT", "BUSINESS", "united", "EWR", 500000, 2000, date=_day(60))
    assert s.rank_surplus < 0 and s.rank_value == s.rank_surplus
    assert s.choice_value is not None and s.choice_value < 0


def test_unpriced_award_has_no_ranking_numbers():
    s = _unpriced()
    assert (s.rank_cash, s.cpp_at_typical, s.rank_surplus, s.rank_surplus_vs(2.0),
            s.surplus_vs(2.0), s.funded_surplus, s.choice_value) == (None,) * 7


def test_age_is_unknown_for_an_unparseable_timestamp():
    s = _unpriced()
    s.c.updated_at = "not a date"
    assert s.age_days is None and s.age_shown is None


def test_watchlist_entries_without_destinations_are_ignored():
    wl = deal_finder.load_watchlist({"watchlist": [{"label": "empty"}, {"dests": ["LIM", "cuz "], "bar": 2}]})
    assert [(w.label, w.dests, w.bar) for w in wl] == [("LIM, cuz ", ["LIM", "CUZ"], 2.0)]


# ── verification edges ───────────────────────────────────────────────────────
def test_seats_aero_rate_limit_stops_trip_lookups_and_flags_the_rest(monkeypatch):
    calls = []

    def _fetch(aid, cabin, points, **k):
        calls.append(aid)
        raise award_trips.QuotaExhausted("429")
    monkeypatch.setattr(award_trips, "fetch", _fetch)
    a = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    b = _scored("JNB", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(61))
    b.c.id = "y"
    logs, cache = [], {}
    deal_finder.attach_trips([a, b], cache, log=logs.append)
    assert len(calls) == 1 and cache["__stop__"] is True
    assert a.trip_unverified and b.trip_unverified  # kept, flagged, not deleted
    assert deal_finder.still_bookable(a, {"united"}) and deal_finder.still_bookable(b, {"united"})
    assert "rate-limited trip lookups" in logs[0]


def test_return_leg_scan_with_nothing_to_scan_or_a_failed_scan(monkeypatch):
    assert deal_finder.scan_return_legs([], {"origins": ["JFK"]}) == ([], False)
    monkeypatch.setattr(award_scanner, "remaining_calls", lambda: 500)

    def _fail(*a, **k):
        raise seats_aero.SearchFailed("500")
    monkeypatch.setattr(award_scanner, "scan", _fail)
    logs = []
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    assert deal_finder.scan_return_legs([s], {"origins": ["EWR"]}, log=logs.append) == ([], False)
    assert "Return-leg scan failed" in logs[0]


def test_return_option_from_the_return_scan_estimates_zero_taxes(monkeypatch):
    monkeypatch.setattr(deal_finder.award_taxes, "load", lambda strict=False: {
        "learned": {deal_finder.award_taxes._key("aeroplan", "BUSINESS"): {"usd": 120.0, "n": 9}}})
    out = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000, date=_day(60))
    back = _award("JFK", "BUSINESS", "aeroplan", 65000, 67)
    back.origin, back.taxes = "ZRH", 0.0
    assert deal_finder.attach_return_options([out], [], [back]) == 1
    assert out.return_option["taxes_usd"] == 120.0  # $0 is never real; the learned figure is used


def test_seeded_route_history_skips_malformed_keys():
    routes = deal_finder.seed_reported_routes({
        "aeroplan|JFK|ZRH|BUSINESS|lots|2027-01-01": "2026-10-01T00:00:00",
        "short|key": "2026-10-01T00:00:00",
        "aeroplan|JFK|LIS|ECONOMY|20000|2027-01-01": "2026-10-01T00:00:00"})
    assert list(routes) == ["aeroplan|JFK|LIS|ECONOMY"]


def test_a_better_alternative_that_is_gone_does_not_replace_the_leader(monkeypatch):
    """The switch to a better program is only made after the alternative is re-checked."""
    leader = _scored("EDI", "BUSINESS", "united", "JFK", 115000, 4000, date=_day(60))
    leader.trip = _trip()
    alt = _scored("EDI", "BUSINESS", "alaska", "JFK", 45000, 4000, date=_day(61))
    alt.c.id = "alt"
    leader.alt_awards = [alt]
    assert alt.choice_value > leader.choice_value
    monkeypatch.setattr(flight_search, "search_round_trip_offers", lambda *a: [_RT(8000.0, 1)])
    monkeypatch.setattr(award_trips, "fetch", lambda *a, **k: None)  # gone from seats.aero
    swaps = deal_finder.promote_better_alternatives([leader], [leader, alt], {}, {}, set(), True,
                                                    log=lambda m: None)
    assert swaps == {} and alt.dropped


def test_a_better_alternative_that_checks_out_replaces_the_leader(monkeypatch):
    leader = _scored("EDI", "BUSINESS", "united", "JFK", 115000, 4000, date=_day(60))
    leader.trip = _trip()
    alt = _scored("EDI", "BUSINESS", "alaska", "JFK", 45000, 4000, date=_day(61))
    alt.c.id = "alt"
    leader.alt_awards = [alt]
    monkeypatch.setattr(flight_search, "search_round_trip_offers", lambda *a: [_RT(8000.0, 1)])
    monkeypatch.setattr(award_trips, "fetch", lambda aid, cabin, points, **k: _trip(current_points=points))
    logs = []
    swaps = deal_finder.promote_better_alternatives([leader], [leader, alt], {}, {}, set(), True,
                                                    log=logs.append)
    assert swaps == {id(leader): alt}
    assert alt.alt_awards == [leader]
    assert logs and logs[0].startswith("Switched EDI BUSINESS from JFK via United to JFK via Alaska")


def test_digest_write_leaves_no_temp_file_when_it_fails(tmp_path):
    path = tmp_path / "digest.json"
    with pytest.raises(TypeError):
        deal_finder._write_json_atomic(str(path), {"bad": object()})
    assert list(tmp_path.iterdir()) == []


def test_previous_digest_is_read_and_a_corrupt_one_reads_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "digest.json"
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(path))
    path.write_text('{"top": [1]}')
    assert deal_finder._load_digest() == {"top": [1]}
    path.write_text("{not json")
    assert deal_finder._load_digest() == {}


# ── run(): failure branches ──────────────────────────────────────────────────
def _run(monkeypatch, **kw):
    logs = []
    out = deal_finder.run(max_lookups=10, top=5, log=logs.append, **{"send_email": False, **kw})
    return out, logs


def test_empty_scan_keeps_yesterdays_digest_and_flags_it(tmp_path, monkeypatch):
    """A dead scan must not overwrite the digest (and its email history) with nothing."""
    _fake_pipeline(tmp_path, monkeypatch, [])
    before = {"top": [{"dest": "ZRH"}], "reported": {"k": "2026-10-20T00:00:00"}}
    (tmp_path / "digest.json").write_text(json.dumps(before))
    out, logs = _run(monkeypatch)
    assert out["scan_failed"] is True and out["top"] == [{"dest": "ZRH"}]
    assert out["reported"] == {"k": "2026-10-20T00:00:00"}
    assert json.loads((tmp_path / "digest.json").read_text()) == before  # untouched on disk
    assert any(m.startswith("::error::No awards scanned") for m in logs)


def test_empty_scan_with_no_previous_digest_returns_an_empty_one(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [])
    out, _ = _run(monkeypatch)
    assert out == {"top": [], "held_miles": [], "watchlist": [], "scan_failed": True}
    assert not (tmp_path / "digest.json").exists()


@pytest.mark.parametrize("extra, expected", [
    ({"quota_exhausted": True}, "::warning::seats.aero's daily call quota ran out mid-scan"),
    ({"truncated": ["aeroplan/BUSINESS"]}, "WARNING: results cut off at the page limit for aeroplan/BUSINESS"),
    ({"aborted": True, "failed": ["a/B", "b/B", "c/B", "d/B"]},
     "::warning::seats.aero kept timing out or erroring, so the scan stopped early after 4 failed"),
    ({"failed": ["united/ECONOMY"]}, "::warning::seats.aero failed for united/ECONOMY even after retries"),
])
def test_a_partial_scan_says_what_is_missing(tmp_path, monkeypatch, extra, expected):
    awards = [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)]
    _fake_pipeline(tmp_path, monkeypatch, awards)
    stats = {"calls": 12, "rows": 1, "stale": 0, "sources": ["aeroplan"], "rate_limit_remaining": "900",
             "candidates": 1, "truncated": [], "quota_exhausted": False, "per_source": {"aeroplan": 1},
             **extra}
    monkeypatch.setattr(award_scanner, "scan", lambda *a, **k: (awards, stats))
    out, logs = _run(monkeypatch)
    assert any(m.startswith(expected) for m in logs), logs
    assert out["top"]  # the run still publishes what it scanned
    # The aborted message must not also print the plain "failed for" one.
    if extra.get("aborted"):
        assert not any("even after retries" in m for m in logs)


def test_partial_scan_warnings_stay_quiet_on_a_full_scan(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)])
    _, logs = _run(monkeypatch)
    assert not any(k in m for m in logs for k in ("quota ran out", "cut off at the page limit",
                                                  "kept timing out", "even after retries",
                                                  "calls left today (a full scan"))


def test_low_seats_aero_quota_is_warned_before_scanning(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)])
    monkeypatch.setattr(award_scanner, "remaining_calls", lambda *a, **k: 249)
    _, logs = _run(monkeypatch)
    assert logs[0].startswith("::warning::Only 249 seats.aero calls left today")


def test_unreadable_tax_table_is_warned_and_left_alone(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)])

    def _unreadable(strict=False):
        if strict:
            raise deal_finder.award_taxes.Unreadable("corrupt")
        return {}
    monkeypatch.setattr(deal_finder.award_taxes, "load", _unreadable)
    monkeypatch.setattr(deal_finder.award_taxes, "save", lambda t: pytest.fail("overwrote the tax table"))
    _, logs = _run(monkeypatch)
    assert any(m.startswith("::warning::Couldn't update the award tax table (corrupt)") for m in logs)


def test_held_miles_in_programs_seats_aero_does_not_cover_are_named(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)])
    monkeypatch.setattr(deal_finder.ledger, "load_program_balances",
                        lambda: {"Southwest Rapid Rewards": 55118})
    _, logs = _run(monkeypatch)
    assert any("not covered by seats.aero: Southwest Rapid Rewards" in m for m in logs)


def _email_run(tmp_path, monkeypatch, configured=True, send=None, digest=None):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 40000, 60)])
    monkeypatch.setattr(deal_finder, "_load_digest", lambda: dict(digest or {}))
    monkeypatch.setattr(deal_finder.deal_email, "is_configured", lambda: configured)
    sent = []
    monkeypatch.setattr(deal_finder.deal_email, "send_digest_email",
                        send or (lambda sections, subject, intro, **kw: sent.append((subject, intro))))
    out, logs = _run(monkeypatch, send_email=True)
    return out, logs, sent


def test_a_failed_email_is_recorded_and_its_deals_are_not_marked_sent(tmp_path, monkeypatch):
    """Otherwise a failed send would silently suppress those deals for 14 days."""
    def _boom(*a, **k):
        raise RuntimeError("SMTP auth failed")
    out, logs, _ = _email_run(tmp_path, monkeypatch, send=_boom)
    assert out["email_failed"] == ["SMTP auth failed"] and out["emailed_new"] == 0
    assert out["reported"] == {} and out["reported_routes"] == {}
    assert out["top"] and out["top"][0]["new"] is True  # still new: tomorrow can send it
    assert any(m == "::error::Deal Finder could not send the digest email: RuntimeError: SMTP auth failed"
               for m in logs)


def test_a_sent_email_records_the_deal_and_route(tmp_path, monkeypatch):
    out, _, sent = _email_run(tmp_path, monkeypatch)
    assert len(sent) == 1 and sent[0][0].startswith("✈️ 1 new award deal: ")
    assert out["emailed_new"] == 1 and out["email_failed"] == []
    assert list(out["reported_routes"]) == ["aeroplan|JFK|ZRH|BUSINESS"]
    assert len(out["reported"]) == 1


def test_gmail_not_configured_warns_and_records_nothing_as_sent(tmp_path, monkeypatch):
    out, logs, sent = _email_run(tmp_path, monkeypatch, configured=False)
    assert sent == [] and out["emailed_new"] == 0 and out["reported"] == {}
    assert "::warning::Gmail isn't configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD): no digest email sent." in logs


def test_a_deal_already_emailed_on_this_exact_date_is_not_new(tmp_path, monkeypatch):
    first, _, _ = _email_run(tmp_path, monkeypatch)
    key = next(iter(first["reported"]))
    again, _, sent = _email_run(tmp_path, monkeypatch, digest={"reported": {key: first["reported"][key]}})
    assert sent == [] and again["top"][0]["new"] is False and again["emailed_new"] == 0


def test_stale_transfer_bonus_list_is_warned_and_mentioned_in_the_email(tmp_path, monkeypatch):
    monkeypatch.setattr(deal_finder.funding, "bonus_list_health", lambda: "Bonuses last checked 40 days ago.")
    _, logs, sent = _email_run(tmp_path, monkeypatch)
    assert "::warning::Bonuses last checked 40 days ago." in logs
    assert sent[0][1].endswith(" Bonuses last checked 40 days ago.")


def test_without_fresh_repricing_the_digest_records_no_repricing(tmp_path, monkeypatch):
    _fake_pipeline(tmp_path, monkeypatch, [_award("ZRH", "BUSINESS", "aeroplan", 60000, 60)])
    monkeypatch.setattr(deal_finder, "FRESH_PRICE_SHORTLIST", False)
    out, _ = _run(monkeypatch)
    assert out["repricing"] == {} and out["top"]


# ── main(): the CLI's exit code is what tells GitHub a run failed ─────────────
def _deal_dict(**over):
    d = {"origin": "JFK", "dest": "ZRH", "cabin": "BUSINESS", "program": "Air Canada Aeroplan",
         "date": "2027-01-10", "other_dates": ["2027-01-12"], "points": 60000, "taxes_usd": 50.0,
         "cash_price": 1800.0, "round_trip_half": 1800.0, "cpp": 2.92, "surplus_usd": 500, "new": True}
    d.update(over)
    return d


def _fake_run(monkeypatch, result=None, exc=None):
    calls = []

    def _run(*args, **kw):
        calls.append((args, kw))
        if exc:
            raise exc
        return result if result is not None else {"held_miles": [], "watchlist": [], "top": []}
    monkeypatch.setattr(deal_finder, "run", _run)
    return calls


def test_cli_defaults_send_email_with_round_trips(monkeypatch):
    calls = _fake_run(monkeypatch)
    assert deal_finder.main([]) == 0
    args, kw = calls[0]
    assert args == (150, 20, True, False, True)
    assert kw == {"max_watch_lookups": 80, "resend": False}


def test_cli_flags_reach_run(monkeypatch):
    calls = _fake_run(monkeypatch)
    assert deal_finder.main(["--max-lookups", "7", "--top", "3", "--max-watch-lookups", "2", "--no-email",
                             "--resend", "--no-round-trip", "--include-planned"]) == 0
    assert calls[0] == ((7, 3, False, True, False), {"max_watch_lookups": 2, "resend": True})


def test_cli_fails_when_seats_aero_is_unavailable(monkeypatch, capsys):
    _fake_run(monkeypatch, exc=seats_aero.NotConfigured("no key"))
    assert deal_finder.main([]) == 1
    assert "::error::seats.aero is unavailable: no key" in capsys.readouterr().out


def test_cli_fails_on_an_empty_scan(monkeypatch, capsys):
    """A green run on yesterday's digest looks exactly like "nothing new today"."""
    _fake_run(monkeypatch, {"scan_failed": True, "top": [], "held_miles": [], "watchlist": []})
    assert deal_finder.main([]) == 1
    assert "this digest is yesterday's" in capsys.readouterr().out


def test_cli_fails_when_the_email_did_not_go_out(monkeypatch):
    _fake_run(monkeypatch, {"email_failed": ["smtp"], "top": [], "held_miles": [], "watchlist": []})
    assert deal_finder.main([]) == 1


def test_cli_prints_every_section(monkeypatch, capsys):
    _fake_run(monkeypatch, {
        "held_miles": [_deal_dict(dest="LIS", program="JetBlue", other_dates=[], new=False)],
        "watchlist": [{"label": "Switzerland", "matched_awards": 1200, "priced": 30,
                       "deals": [_deal_dict(watch_surplus_usd=900)]}],
        "top": [_deal_dict(dest="FRA", round_trip_half=None, cash_price=3000.0)]})
    assert deal_finder.main([]) == 0
    out = capsys.readouterr().out
    assert "✅ Book now with miles you already hold:" in out
    assert "⭐ Switzerland: 1 deal(s) from 1,200 matching awards (30 priced)" in out
    assert "Top 1 (RT = valued at half a round trip):" in out
    lines = out.splitlines()
    watch_line = next(line for line in lines if "JFK-ZRH" in line)
    assert " +1d " in watch_line and " RT " in watch_line and "+$900" in watch_line
    assert watch_line.endswith("  NEW")
    held_line = next(line for line in lines if "JFK-LIS" in line)
    assert not held_line.endswith("NEW") and "+$500" in held_line


# ── the rest: interactive preview, re-pricing failures, selection warning ────
def test_card_preview_with_no_programs_spends_nothing(monkeypatch):
    monkeypatch.setattr(award_scanner, "scan", lambda *a, **k: pytest.fail("scanned"))
    assert deal_finder.preview_sources([]) == {"deals": [], "candidates": 0, "calls": 0}


def test_card_preview_caps_its_seats_aero_lookups(tmp_path, monkeypatch):
    """A click on the roadmap page shares the 1,000/day with the scheduled run."""
    dests = ("ZRH", "FRA", "CDG", "LHR", "MAD", "FCO", "ATH", "LIS", "MUC", "VIE", "IST", "DUB")
    awards = [_award(d, "BUSINESS", "aeroplan", 15000, 60 + i) for i, d in enumerate(dests)]
    _fake_pipeline(tmp_path, monkeypatch, awards)
    trips = []
    real_fetch = award_trips.fetch
    monkeypatch.setattr(award_trips, "fetch", lambda aid, *a, **k: trips.append(aid) or real_fetch(aid, *a, **k))
    monkeypatch.setattr(deal_finder, "INTERACTIVE_TRIP_LOOKUPS", 4)
    out = deal_finder.preview_sources(["aeroplan"], max_lookups=60, top=10, log=lambda m: None)
    assert len(trips) == 4
    assert out["calls"] == 12 + 4 and out["candidates"] == 12
    assert out["deals"] and all(d["source"] == "aeroplan" for d in out["deals"])


def test_fresh_repricing_keeps_the_fare_when_google_errors(monkeypatch):
    def _boom(*a, **k):
        raise flight_search.SearchFailed("x")
    monkeypatch.setattr(cash_quotes, "get_quote", _boom)
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5000, date=_day(60))
    unpriced = _unpriced()
    logs = []
    stats = deal_finder.reprice_fresh([s, unpriced], log=logs.append)
    assert stats == {"repriced": 0, "changed": 0, "failed": 1} and s.cash == 5000.0
    assert logs == []  # nothing re-priced, nothing to report


def test_selection_out_of_rounds_says_so_in_the_log(monkeypatch):
    monkeypatch.setattr(award_trips, "fetch", lambda aid, cabin, points, **k: _trip(current_points=points))
    scored = [_scored(d, "BUSINESS", "aeroplan", "JFK", 60000, 3000) for d in ("ZRH", "FRA", "CAI")]
    for s in scored:
        s.c.id = f"{s.c.dest}:id"
    logs = []
    deal_finder.verify_selection(scored, lambda ls: ls[:2], rt_cache=None, trip_cache={},
                                 sources_reporting_seats=set(), round_trip=False, max_rounds=0,
                                 log=logs.append)
    assert logs[0] == ("::warning::Selection didn't settle in 0 rounds; checking the 2 unchecked "
                       "deal(s) it would publish")


def test_slow_selection_logs_what_each_round_checked(monkeypatch):
    """Oct 23-25: the main selection used 12 rounds three days running, and the log
    gave only the totals, so nothing showed which deals kept it going. A slow
    selection now logs, per round, what it checked and what happened to each."""
    # Every check turns out to be a connection on a route with nonstops elsewhere, so
    # each checked leader loses to the next unchecked date on its card.
    monkeypatch.setattr(award_trips, "fetch",
                        lambda aid, cabin, points, **k: _trip(nonstop=False, current_points=points))
    scored = []
    for d in ("ZRH", "FRA"):
        for i in range(4):
            s = _scored(d, "BUSINESS", "aeroplan", "JFK", 60000, 3000 - 10 * i)
            s.c.id = f"{d}:{i}"
            scored.append(s)
    logs = []
    deal_finder.verify_selection(scored, lambda ls: ls[:2], rt_cache=None, trip_cache={},
                                 sources_reporting_seats=set(), round_trip=False, max_rounds=1,
                                 log=logs.append)
    rounds = [m for m in logs if m.startswith("Selection rounds:")]
    assert len(rounds) == 1, logs
    assert "r1 checked 2" in rounds[0] and "ZRH" in rounds[0] and "FRA" in rounds[0], rounds[0]


def test_quick_selection_logs_no_round_detail(monkeypatch):
    monkeypatch.setattr(award_trips, "fetch", lambda aid, cabin, points, **k: _trip(current_points=points))
    scored = [_scored(d, "BUSINESS", "aeroplan", "JFK", 60000, 3000) for d in ("ZRH", "FRA")]
    for s in scored:
        s.c.id = f"{s.c.dest}:id"
    logs = []
    deal_finder.verify_selection(scored, lambda ls: ls[:2], rt_cache=None, trip_cache={},
                                 sources_reporting_seats=set(), round_trip=False, log=logs.append)
    assert not any(m.startswith("Selection rounds:") for m in logs), logs
