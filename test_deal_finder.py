import math

import pytest

import award_scanner
import cash_quotes
import deal_finder
import fare_model


def _item(**over):
    item = {
        "ID": "abc", "Source": "aeroplan", "Date": "2027-01-10", "UpdatedAt": "2099-01-01T00:00:00Z",
        "TaxesCurrency": "CAD",
        "JAvailable": True, "JMileageCostRaw": 60000, "JTotalTaxesRaw": 8091,
        "JRemainingSeatsRaw": 2, "JDirectRaw": True, "JAirlinesRaw": "LX",
        "YAvailable": False,
        "Route": {"OriginAirport": "JFK", "DestinationAirport": "ZRH", "Distance": 3920},
    }
    item.update(over)
    return item


def test_parse_converts_taxes_from_cents_and_skips_unavailable_cabins():
    c = award_scanner._parse(_item(), "BUSINESS")
    assert (c.points, c.taxes, c.taxes_currency, c.seats, c.direct) == (60000, 80.91, "CAD", 2, True)
    assert c.program == "Air Canada Aeroplan"
    assert award_scanner._parse(_item(), "ECONOMY") is None


def test_transferable_sources_only_cover_active_pools():
    sources = award_scanner.transferable_sources()
    assert "aeroplan" in sources and "united" in sources      # Chase UR (held)
    assert "qatar" in sources and "alaska" in sources            # Bilt (points kept, no card needed)
    assert "finnair" not in sources and "qantas" not in sources  # only via planned cards (Venture X)
    assert "finnair" in award_scanner.transferable_sources(include_planned=True)


def _rows():
    rows = []
    for dest, miles_mult in (("LHR", 1.0), ("CDG", 1.05), ("MAD", 1.1), ("FCO", 1.2), ("ATH", 1.4),
                             ("PTY", 0.7), ("BOG", 0.8), ("LIM", 1.1), ("SCL", 1.5), ("CAI", 1.6)):
        for cabin, mult in (("ECONOMY", 1.0), ("BUSINESS", 3.5)):
            for i in range(3):
                rows.append({"origin": "JFK", "dest": dest, "cabin": cabin,
                             "price": 400 * miles_mult * mult * (1 + 0.05 * i), "at": ""})
    return rows


def test_fare_model_orders_cabins_and_uses_route_data():
    m = fare_model.FareModel(_rows())
    econ, biz = m.estimate("JFK", "LHR", "ECONOMY"), m.estimate("JFK", "LHR", "BUSINESS")
    assert biz.median_price > 2.5 * econ.median_price
    assert econ.basis == "route" and econ.route_obs == 3
    unseen = m.estimate("JFK", "NRT", "BUSINESS")
    assert unseen.basis == "model" and unseen.sigma_log > econ.sigma_log
    first = m.estimate("JFK", "LHR", "FIRST")
    assert first.median_price > biz.median_price and first.sigma_log > biz.sigma_log


def test_estimate_probability_is_lognormal():
    e = fare_model.Estimate(median_price=1000.0, sigma_log=0.3, route_obs=0, basis="model")
    assert e.prob_at_least(1000.0) == pytest.approx(0.5)
    assert e.prob_at_least(1000.0 * math.exp(0.3)) == pytest.approx(0.1587, abs=1e-3)
    assert e.prob_at_least(0) == 1.0


def _scored(dest, cabin, source, origin, points, cash, date="2027-01-10"):
    c = award_scanner.AwardCandidate(
        id="x", source=source, program=source.title(), origin=origin, dest=dest, date=date, cabin=cabin,
        points=points, taxes=50.0, taxes_currency="USD", seats=2, direct=True, airlines="", distance=3000,
        updated_at="")
    est = fare_model.Estimate(cash, 0.3, 0, "model")
    s = deal_finder.Scored(c, 50.0, est, 2.0 if cabin == "BUSINESS" else 1.5, 0.0, 0.5)
    deal_finder._apply_quote(s, cash_quotes.Quote(origin, dest, cabin, date, cash, None, "test", ""))
    return s


def test_shortlist_collapses_same_trip_and_reserves_economy_slots():
    scored = [
        _scored("CAI", "BUSINESS", "aeroplan", "EWR", 75000, 3400),
        _scored("CAI", "BUSINESS", "aeroplan", "EWR", 75000, 3300, date="2027-01-12"),
        _scored("CAI", "BUSINESS", "united", "EWR", 88000, 3400),
        _scored("CAI", "BUSINESS", "aeroplan", "JFK", 75000, 2900),
        _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 2800),
        _scored("FRA", "BUSINESS", "aeroplan", "JFK", 60000, 2300),
        _scored("LIR", "ECONOMY", "jetblue", "JFK", 12000, 700),
        _scored("PTY", "ECONOMY", "united", "EWR", 20000, 200),  # below the bar -> excluded
    ]
    top = deal_finder.shortlist(scored, top=3, min_economy=1)
    assert [(s.c.dest, s.c.cabin) for s in top] == [("CAI", "BUSINESS"), ("ZRH", "BUSINESS"), ("LIR", "ECONOMY")]
    cai = top[0]
    assert cai.other_dates == ["2027-01-12"]
    assert len(cai.alternatives) == 2 and any("United" in a for a in cai.alternatives)


def test_first_class_is_priced_against_business_fare(monkeypatch):
    seen = []

    def _quote(origin, dest, date, cabin, **k):
        seen.append(cabin)
        return cash_quotes.Quote(origin, dest, cabin, date, 5000.0, None, "test", "2026-01-01T00:00:00Z")

    monkeypatch.setattr(cash_quotes, "get_quote", _quote)
    monkeypatch.setattr(cash_quotes, "load", lambda: [])
    s = _scored("DEL", "FIRST", "united", "EWR", 220000, 1.0)
    s.cash = s.cpp = s.surplus = None
    s.p_great = 0.9
    deal_finder.price_promising([s], max_lookups=5, log=lambda m: None)
    assert seen == ["BUSINESS"] and s.cash == 5000.0


def test_watch_entry_matching_and_bar():
    w = deal_finder.WatchEntry.from_config({"label": "Japan", "dests": ["nrt", "HND"], "cabins": ["business"],
                                            "start": "2027-03-20", "end": "2027-04-10", "bar": 1.8})
    c = _scored("NRT", "BUSINESS", "aeroplan", "JFK", 75000, 3000, date="2027-03-25").c
    assert w.matches(c) and w.bar_for("BUSINESS", "Air Canada Aeroplan") == 1.8
    assert not w.matches(_scored("NRT", "BUSINESS", "aeroplan", "JFK", 75000, 3000, date="2027-05-01").c)
    assert not w.matches(_scored("NRT", "ECONOMY", "aeroplan", "JFK", 35000, 900, date="2027-03-25").c)
    # No bar set: falls back to the program's own bar (Aeroplan 1.5c x 1.6).
    assert deal_finder.WatchEntry.from_config({"dests": ["LIS"]}).bar_for(
        "ECONOMY", "Air Canada Aeroplan") == pytest.approx(2.4)


def test_watch_report_uses_entry_bar_even_below_the_usual_bar():
    w = deal_finder.WatchEntry.from_config({"label": "Lisbon", "dests": ["LIS"], "bar": 1.2})
    s = _scored("LIS", "ECONOMY", "flyingblue", "JFK", 30000, 450)  # 1.33c: below 1.5, above 1.2
    s.watch = [w]
    assert deal_finder.group_leaders([s]) == []
    (group,) = deal_finder.watch_report([s], [w], rt_cache={}, round_trip=False)
    assert len(group["deals"]) == 1 and group["deals"][0][1]["watch_bar"] == 1.2


class _RT:
    def __init__(self, price, stops):
        self.price_usd, self.stops = price, stops


def _trip(**over):
    import award_trips
    base = dict(flights=["X1 A–B"], connections=[], duration_min=600, departs_at="2027-02-09T10:00:00Z",
                arrives_at="2027-02-09T18:00:00Z", leg_cabins=["business"], mixed_cabin=False,
                lower_cabin_legs=[], carriers="", booking_url=None, booking_label=None,
                other_itineraries=0, airport_changes=[], stops=0, nonstop=True, seats=2,
                current_points=88000, price_matches=True)
    base.update(over)
    return award_trips.TripInfo(**base)


def test_round_trip_half_replaces_higher_one_way_fare(monkeypatch):
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5084, date="2027-02-09")
    s.one_way_cash = 5084.0
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers",
                        lambda *a, **k: [_RT(5038.0, 1), _RT(3000.0, 3)])
    s.trip = _trip(nonstop=True)  # a confirmed nonstop compares against <=1-stop fares
    from datetime import date
    assert deal_finder.apply_round_trip(s, {}, today=date(2026, 9, 16))
    assert s.cash == 2519.0 and s.round_trip_half == 2519.0  # 3-stop RT ignored for a nonstop award
    assert "round trip" in s.cash_basis and s.cpp == pytest.approx((2519 - 50) / 88000 * 100)


def test_round_trip_does_not_raise_a_cheaper_one_way(monkeypatch):
    s = _scored("LIS", "ECONOMY", "flyingblue", "JFK", 20000, 292, date="2026-11-12")
    s.one_way_cash = 292.0
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers", lambda *a, **k: [_RT(700.0, 1)])
    from datetime import date
    deal_finder.apply_round_trip(s, {}, today=date(2026, 9, 16))
    assert s.cash == 292.0 and s.round_trip_half == 350.0


def test_watch_budget_is_shared_round_robin(monkeypatch):
    big = deal_finder.WatchEntry.from_config({"label": "Caribbean", "dests": ["SJU"]})
    small = deal_finder.WatchEntry.from_config({"label": "Peru", "dests": ["LIM"]})
    cands = []
    for i in range(20):
        s = _scored("SJU", "ECONOMY", "jetblue", "JFK", 10000, 1.0, date=f"2027-01-{i + 1:02d}")
        s.cash = s.cpp = s.surplus = None
        s.watch, s.p_watch, s.p_great = [big], 0.9, 0.9
        cands.append(s)
    peru = _scored("LIM", "ECONOMY", "united", "JFK", 20000, 1.0)
    peru.cash = peru.cpp = peru.surplus = None
    peru.watch, peru.p_watch, peru.p_great = [small], 0.3, 0.3
    cands.append(peru)
    looked_up = []

    def _quote(origin, dest, date, cabin, **k):
        looked_up.append(dest)
        return cash_quotes.Quote(origin, dest, cabin, date, 500.0, None, "test", "2026-09-16T00:00:00Z")

    monkeypatch.setattr(cash_quotes, "get_quote", _quote)
    monkeypatch.setattr(cash_quotes, "load", lambda: [])
    stats = deal_finder.price_promising(cands, max_lookups=0, log=lambda m: None,
                                        max_watch_lookups=2, watchlist=[big, small])
    assert sorted(looked_up) == ["LIM", "SJU"] and stats["watch_live"] == 2 and stats["live"] == 0


def test_held_miles_report_only_includes_fully_covered_awards():
    cheap = _scored("LIR", "ECONOMY", "jetblue", "JFK", 15000, 700)
    pricey = _scored("PTY", "ECONOMY", "jetblue", "JFK", 30000, 900)
    for s in (cheap, pricey):
        s.held_miles = 22516
    (only,) = deal_finder.held_miles_report([cheap, pricey], rt_cache={}, round_trip=False)
    assert only is cheap and only.bookable_now
    d = pricey.to_dict()
    assert d["bookable_now"] is False and d["top_up_needed"] == 30000 - 22516


def test_held_program_sources_are_added_to_scan():
    assert award_scanner.sources_for_programs(["American Airlines AAdvantage", "Delta SkyMiles",
                                               "Southwest Rapid Rewards"]) == ["american", "delta"]


def test_awards_with_unreported_seat_count_still_rank():
    s = _scored("ANU", "ECONOMY", "american", "JFK", 9500, 388)
    s.c.seats = 0  # American always reports 0 ("unknown")
    assert deal_finder.group_leaders([s]) == [s]


def test_mixed_cabin_and_airport_change_rank_lower():
    import award_trips
    base = dict(flights=["X1 A–B"], connections=[], duration_min=600, departs_at="", arrives_at="",
                leg_cabins=["business"], carriers="", booking_url=None, booking_label=None, other_itineraries=0)
    clean = _scored("ZRH", "BUSINESS", "united", "EWR", 88000, 3000)
    mixed = _scored("ZRH", "BUSINESS", "united", "EWR", 88000, 3000)
    change = _scored("ZRH", "BUSINESS", "united", "EWR", 88000, 3000)
    clean.trip = award_trips.TripInfo(**base, mixed_cabin=False, lower_cabin_legs=[], airport_changes=[])
    mixed.trip = award_trips.TripInfo(**base, mixed_cabin=True, lower_cabin_legs=["X1 (economy)"], airport_changes=[])
    change.trip = award_trips.TripInfo(**base, mixed_cabin=False, lower_cabin_legs=[], airport_changes=["DCA → IAD"])
    assert clean.rank_value > change.rank_value > mixed.rank_value


def test_round_trip_falls_back_to_a_shorter_stay_near_the_booking_window(monkeypatch):
    from datetime import date, timedelta
    today = date(2026, 9, 22)
    depart = today + timedelta(days=327)  # a 7-night return is past the 330-day window
    asked = []

    def _rt(o, d, dep, ret, cabin):
        asked.append(ret)
        return [_RT(4000.0, 1)]

    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers", _rt)
    s = _scored("ATH", "BUSINESS", "united", "EWR", 88000, 3500, date=depart.isoformat())
    s.one_way_cash = 3500.0
    assert deal_finder.apply_round_trip(s, {}, today=today) is True
    assert asked == [(depart + timedelta(days=2)).isoformat()]
    assert s.cash == 2000.0 and s.rt_unavailable is False


def test_no_round_trip_possible_marks_and_demotes(monkeypatch):
    from datetime import date, timedelta
    today = date(2026, 9, 22)
    s = _scored("ATH", "BUSINESS", "united", "EWR", 88000, 3500,
                date=(today + timedelta(days=330)).isoformat())
    s.one_way_cash = 3500.0
    before = s.rank_value
    assert deal_finder.apply_round_trip(s, {}, today=today) is False
    assert s.rt_unavailable is True and s.rank_value < before


def test_still_bookable_drops_gone_repriced_and_soldout_awards():
    gone = _scored("ATH", "BUSINESS", "united", "EWR", 88000, 3500)
    repriced = _scored("ACC", "ECONOMY", "flyingblue", "JFK", 33000, 900)
    soldout = _scored("JNB", "BUSINESS", "united", "EWR", 88000, 3500)
    unknown_seats = _scored("GCM", "ECONOMY", "american", "JFK", 10000, 320)
    ok = _scored("DUB", "BUSINESS", "alaska", "JFK", 55000, 2600)
    gone.trip = None
    repriced.trip = _trip(price_matches=False, current_points=91000)
    soldout.trip = _trip(seats=0)
    unknown_seats.trip = _trip(seats=0)  # American always reports 0 = unknown
    ok.trip = _trip(seats=3)
    reporting = {"united", "flyingblue", "alaska"}
    assert [deal_finder.still_bookable(s, reporting) for s in (gone, repriced, soldout, unknown_seats, ok)] \
        == [False, False, False, True, True]


def test_a_demoted_leader_does_not_take_its_destination_group_down(monkeypatch):
    # Leader looks best on the one-way fare but is gone; the runner-up should be reported.
    leader = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000)
    runner_up = _scored("ZRH", "BUSINESS", "aeroplan", "EWR", 50000, 2000)
    leader.c.id, runner_up.c.id = "leader", "runner"
    monkeypatch.setattr(deal_finder.award_trips, "fetch",
                        lambda aid, *a, **k: None if aid == "leader" else _trip())
    out = deal_finder.verify_leaders([leader, runner_up], [leader, runner_up], None, {},
                                     {"aeroplan"}, round_trip=False)
    assert [s.c.origin for s in out] == ["EWR"]


def test_a_failed_lookup_keeps_the_deal_but_flags_it(monkeypatch):
    """seats.aero timing out is not proof the award is gone: dropping it silently
    empties the digest whenever the tail of a run gets rate-limited."""
    s = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000)
    monkeypatch.setattr(deal_finder.award_trips, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(deal_finder.award_trips.LookupFailed("429")))
    deal_finder.attach_trips([s], {})
    assert s.trip_unverified is True and s.trip is None
    assert deal_finder.still_bookable(s, {"aeroplan"}) is True
    assert s.to_dict()["trip_unverified"] is True


def test_resend_keeps_the_report_history(tmp_path, monkeypatch):
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(tmp_path / "digest.json"))
    import json
    old = {"reported": {"a|b|c|BUSINESS|60000|2027-01-01": "2099-01-01T00:00:00"}}
    (tmp_path / "digest.json").write_text(json.dumps(old))
    monkeypatch.setattr(deal_finder.award_scanner, "scan", lambda *a, **k: ([], {
        "calls": 0, "rows": 0, "stale": 0, "sources": [], "rate_limit_remaining": "1", "candidates": 0,
        "truncated": []}))
    class _Model:
        def summary(self):
            return "stub"

    monkeypatch.setattr(deal_finder.fare_model, "FareModel", lambda *a, **k: _Model())
    monkeypatch.setattr(deal_finder, "score_candidates", lambda *a, **k: [])
    out = deal_finder.run(max_lookups=0, top=5, send_email=False, resend=True, max_watch_lookups=0,
                          log=lambda m: None)
    assert out["reported"] == old["reported"]


def test_top_list_is_not_filled_by_one_program():
    leaders = []
    for i in range(10):
        s = _scored("D%d" % i, "BUSINESS", "united", "EWR", 88000, 3000 + i)
        leaders.append(s)
    for i in range(3):
        s = _scored("E%d" % i, "BUSINESS", "aeroplan", "JFK", 60000, 2000 + i)
        leaders.append(s)
    leaders.sort(key=lambda s: -s.surplus)
    picked = deal_finder.pick_top(leaders, top=10, min_economy=0)
    assert sum(1 for s in picked if s.c.source == "united") == deal_finder.MAX_PER_PROGRAM
    assert any(s.c.source == "aeroplan" for s in picked)


def test_estimate_drift_summarises_error_against_real_fares():
    assert deal_finder.estimate_drift([]) == {}
    d = deal_finder.estimate_drift([0.05, 0.10, 0.20, 0.30, 1.00])
    assert d == {"n": 5, "median_pct": 20.0, "p90_pct": 100.0}


def test_live_lookups_record_how_far_off_the_estimate_was(monkeypatch):
    s = _scored("LIS", "ECONOMY", "flyingblue", "JFK", 20000, 1.0)
    s.cash = s.cpp = s.surplus = None
    s.est = fare_model.Estimate(400.0, 0.3, 0, "model")
    s.p_great = 0.9
    monkeypatch.setattr(cash_quotes, "load", lambda: [])
    monkeypatch.setattr(cash_quotes, "get_quote",
                        lambda o, d, dt, cab, **k: cash_quotes.Quote(o, d, cab, dt, 500.0, None, "t",
                                                                    "2026-01-01T00:00:00Z"))
    stats = deal_finder.price_promising([s], max_lookups=5, log=lambda m: None)
    assert deal_finder.estimate_drift(stats["estimate_errors"]) == {"n": 1, "median_pct": 25.0, "p90_pct": 25.0}


def test_round_trip_price_survives_the_later_fare_rematch(monkeypatch):
    """Regression: attach_trips re-matched the one-way fare and wiped the cheaper
    round-trip valuation, inflating every CPP in the digest."""
    s = _scored("CPT", "BUSINESS", "flyingblue", "JFK", 115000, 5808)
    s.quote = cash_quotes.Quote("JFK", "CPT", "BUSINESS", s.c.date, 5808.0, None, "test",
                                "2026-09-23T00:00:00Z", offers=[[5808.0, 1, "AF"]])
    s.one_way_cash = 5808.0
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers",
                        lambda *a, **k: [_RT(6954.0, 1)])
    from datetime import date
    deal_finder.apply_round_trip(s, {}, today=date(2026, 9, 23))
    assert s.cash == 3477.0 and "round trip" in s.cash_basis
    monkeypatch.setattr(deal_finder.award_trips, "fetch", lambda *a, **k: _trip(nonstop=False, stops=1))
    deal_finder.attach_trips([s], {})
    assert s.cash == 3477.0, "the round-trip valuation must survive the fare re-match"
    assert s.cpp == pytest.approx((3477.0 - 50.0) / 115000 * 100)


def test_a_promoted_leader_is_verified_before_being_reported(monkeypatch):
    """Regrouping can promote a deal the first verification pass never saw."""
    leader = _scored("TPE", "BUSINESS", "united", "EWR", 110000, 4000)
    backup = _scored("TPE", "BUSINESS", "united", "JFK", 110000, 3500)
    leader.c.id, backup.c.id = "leader", "backup"
    leader.trip = None
    checked = []

    def _fetch(aid, cabin, points, **k):
        checked.append(points)
        return None if len(checked) == 1 else _trip()

    monkeypatch.setattr(deal_finder.award_trips, "fetch", _fetch)
    out = deal_finder.verify_leaders([leader, backup], [leader], None, {}, {"united"}, round_trip=False)
    assert [s.c.origin for s in out] == ["JFK"]  # unverified leader dropped, backup checked and kept
    assert len(checked) == 2


def test_shortlist_is_repriced_on_its_own_date_not_a_borrowed_one(monkeypatch):
    """Reported deals are selected because their fare came in high, and most were
    priced from a nearby date; re-checking found all 9 sampled deals cheaper."""
    asked = []

    def _quote(origin, dest, date_str, cabin, allow_approx=True, **k):
        asked.append(allow_approx)
        return cash_quotes.Quote(origin, dest, cabin, date_str, 1190.0, None, "test",
                                 "2026-09-23T00:00:00Z", offers=[[1190.0, 1, "AT"]])

    monkeypatch.setattr(cash_quotes, "get_quote", _quote)
    s = _scored("CMN", "BUSINESS", "flyingblue", "JFK", 60000, 3685)
    stats = deal_finder.reprice_fresh([s], log=lambda m: None)
    assert asked == [False]  # never a borrowed quote
    assert stats == {"repriced": 1, "changed": 1, "failed": 0}
    assert s.cash == 1190.0 and s.cpp == pytest.approx((1190.0 - 50.0) / 60000 * 100)


def test_repricing_keeps_a_cheaper_round_trip(monkeypatch):
    s = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000)
    s.round_trip_half, s.rt_stay_nights = 1500.0, 7
    monkeypatch.setattr(cash_quotes, "get_quote",
                        lambda o, d, dt, cab, **k: cash_quotes.Quote(o, d, cab, dt, 2800.0, None, "t",
                                                                    "2026-09-23T00:00:00Z",
                                                                    offers=[[2800.0, 1, "LX"]]))
    deal_finder.reprice_fresh([s], log=lambda m: None)
    assert s.cash == 1500.0 and "round trip" in s.cash_basis


def test_exploration_prices_routes_the_model_knows_least(monkeypatch):
    """Without this, the gate only ever prices what the model already likes, so it
    never learns where it is wrong."""
    known = _scored("LIS", "ECONOMY", "flyingblue", "JFK", 20000, 1.0)
    unknown = _scored("NBO", "BUSINESS", "united", "EWR", 88000, 1.0)
    for s in (known, unknown):
        s.cash = s.cpp = s.surplus = None
        s.p_great = 0.0  # both would be skipped by the normal gate
    unknown.est = fare_model.Estimate(2000.0, 0.45, 0, "model")
    known.est = fare_model.Estimate(400.0, 0.10, 3, "route")
    asked = []
    monkeypatch.setattr(cash_quotes, "load", lambda: [
        {"origin": "JFK", "dest": "LIS", "cabin": "ECONOMY",
         "fetched_at": deal_finder.datetime.now(deal_finder.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}])
    monkeypatch.setattr(cash_quotes, "get_quote", lambda o, d, dt, cab, **k: asked.append(d) or
                        cash_quotes.Quote(o, d, cab, dt, 1500.0, None, "t", "2026-09-23T00:00:00Z"))
    stats = deal_finder.price_promising([known, unknown], max_lookups=4, log=lambda m: None)
    assert asked == ["NBO"]  # LIS was quoted recently; NBO is the unknown cell
    assert stats["explore_live"] == 1


def test_record_history_keeps_the_cheapest_per_route_month(tmp_path):
    import award_history
    import award_scanner
    path = str(tmp_path / "hist.json")

    def cand(points, date_str):
        return award_scanner.AwardCandidate(id="i", source="aeroplan", program="Air Canada Aeroplan",
                                            origin="JFK", dest="ZRH", date=date_str, cabin="BUSINESS",
                                            points=points, taxes=50.0, taxes_currency="USD", seats=1,
                                            direct=True, airlines="LX", distance=3900, updated_at="")
    hist = award_history.record([cand(70000, "2027-03-04"), cand(60000, "2027-03-19"),
                                       cand(90000, "2027-04-02")], path=path)
    today = list(hist)[0]
    assert hist[today]["aeroplan|JFK|ZRH|BUSINESS|2027-03"] == 60000
    assert hist[today]["aeroplan|JFK|ZRH|BUSINESS|2027-04"] == 90000
    award_history.record([cand(55000, "2027-03-10")], path=path)  # rerun same day keeps the min
    assert award_history.record([], path=path)[today]["aeroplan|JFK|ZRH|BUSINESS|2027-03"] == 55000


def test_history_only_records_changes(tmp_path):
    import award_history
    import award_scanner
    path = str(tmp_path / "hist.json")
    c = award_scanner.AwardCandidate(id="i", source="united", program="United MileagePlus", origin="EWR",
                                     dest="LIS", date="2027-05-04", cabin="BUSINESS", points=88000,
                                     taxes=10.0, taxes_currency="USD", seats=1, direct=True, airlines="UA",
                                     distance=3400, updated_at="")
    award_history.record([c], path=path)
    import json
    hist = json.load(open(path))
    day = list(hist)[0]
    hist["2020-01-01"] = hist.pop(day)  # pretend yesterday recorded the same price
    json.dump(hist, open(path, "w"))
    again = award_history.record([c], path=path)
    assert again[list(again)[-1]] == {}, "an unchanged price should not be recorded again"


def test_health_warnings_fire_on_a_useless_run():
    msgs = []
    deal_finder._warn_on_health({"live": 10, "watch_live": 0, "explore_live": 0, "no_fare": 6,
                                 "skipped_low_p": 5000}, {"rows": 1000, "stale": 400}, 100, msgs.append)
    joined = " ".join(msgs)
    assert "returned no fare" in joined and "lookup budget" in joined and "freshness limit" in joined


def test_route_history_percentile_and_ranking(tmp_path, monkeypatch):
    import award_history
    path = str(tmp_path / "h.json")
    hist = {}
    for i in range(20):  # 20 days at 90k, then today's 60k award
        hist[f"2026-09-{i + 1:02d}"] = {"aeroplan|JFK|ZRH|BUSINESS|2027-03": 90000} if i == 0 else {}
    award_history._write_atomic(path, hist)
    ser = award_history.series(award_history.load(path), window_days=3650)
    pct, days = award_history.percentile(ser, "aeroplan|JFK|ZRH|BUSINESS|2027-03", 60000)
    assert pct == 1.0 and days == 20  # cheapest it has ever been here
    assert award_history.percentile(ser, "aeroplan|JFK|ZRH|BUSINESS|2027-03", 120000)[0] == 0.0
    assert award_history.percentile(ser, "unknown|key|x|y|2027-03", 1000) == (None, 0)

    cheap = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000)
    usual = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000)
    cheap.history_pct, cheap.history_days = 1.0, 20
    usual.history_pct, usual.history_days = 0.2, 20
    assert cheap.rank_value > usual.rank_value


def test_return_leg_is_paired_from_the_same_scan():
    out = _scored("ZRH", "BUSINESS", "aeroplan", "JFK", 60000, 3000, date="2027-03-10")
    back_early = _scored("JFK", "BUSINESS", "aeroplan", "ZRH", 70000, 3000, date="2027-03-11")  # too soon
    back_good = _scored("JFK", "BUSINESS", "aeroplan", "ZRH", 65000, 3000, date="2027-03-18")
    back_other = _scored("JFK", "BUSINESS", "united", "ZRH", 50000, 3000, date="2027-03-18")  # other program
    assert deal_finder.attach_return_options([out], [back_early, back_good, back_other]) == 1
    assert out.return_option["points"] == 65000
    assert out.return_option["round_trip_points"] == 125000


def test_source_drop_and_stale_training_warnings():
    msgs = []
    deal_finder._warn_on_source_drop({"per_source": {"aeroplan": 100}},
                                     {"scan": {"per_source": {"aeroplan": 5000}}}, msgs.append)
    deal_finder._warn_on_stale_training([{"at": "2020-01-01T00:00:00Z"}] * 300, msgs.append)
    joined = " ".join(msgs)
    assert "aeroplan returned" in joined and "last two weeks" in joined
