from datetime import date, timedelta

import deal_finder
import route_search


def _deal(dest, origin, points, cash, date_str, other_dates=()):
    import award_scanner
    import fare_model
    c = award_scanner.AwardCandidate(id="x", source="aeroplan", program="Air Canada Aeroplan", origin=origin,
                                     dest=dest, date=date_str, cabin="BUSINESS", points=points, taxes=50.0,
                                     taxes_currency="USD", seats=2, direct=True, airlines="AC", distance=3000,
                                     updated_at="")
    s = deal_finder.Scored(c, 50.0, fare_model.Estimate(cash, 0.3, 0, "model"), 2.0, 0.0, 0.5)
    s.cash, s.one_way_cash = cash, cash
    s.cpp = (cash - 50.0) / points * 100
    s.surplus = (cash - 50.0) - points * 2.0 / 100
    s.other_dates = list(other_dates)
    return s


def test_pair_picks_outbound_and_return_dates_together(monkeypatch):
    """Regression: the pair filter allowed an alternative outbound date, then the
    booking date was computed from the leader date only, raising ValueError."""
    out = route_search.RouteResult([_deal("LIS", "JFK", 60000, 2000, "2027-01-10",
                                          other_dates=["2027-01-02"])], 1, 1, 1)
    back = route_search.RouteResult([_deal("JFK", "LIS", 60000, 1800, "2027-01-05")], 1, 1, 1)
    monkeypatch.setattr(route_search, "search", lambda *a, **k: out if a[1] != "JFK" else back)
    monkeypatch.setattr(route_search.flight_search, "search_round_trip_offers",
                        lambda *a, **k: (_ for _ in ()).throw(route_search.flight_search.SearchFailed("no")))
    r = route_search.search_pair("JFK", "LIS", ["BUSINESS"], date(2027, 1, 1), date(2027, 1, 15),
                                 date(2027, 1, 1), date(2027, 1, 15))
    assert r.best_pair is not None
    assert r.combined_cpp > 1  # cents per point, not dollars per point


def test_pair_is_none_when_no_return_follows_an_outbound(monkeypatch):
    out = route_search.RouteResult([_deal("LIS", "JFK", 60000, 2000, "2027-01-10")], 1, 1, 1)
    back = route_search.RouteResult([_deal("JFK", "LIS", 60000, 1800, "2027-01-05")], 1, 1, 1)
    monkeypatch.setattr(route_search, "search", lambda *a, **k: out if a[1] != "JFK" else back)
    r = route_search.search_pair("JFK", "LIS", ["BUSINESS"], date(2027, 1, 1), date(2027, 1, 15),
                                 date(2027, 1, 1), date(2027, 1, 15))
    assert r.best_pair is None


def test_scan_ignores_an_unknown_cabin(monkeypatch):
    import award_scanner
    calls = []

    class _Resp:
        status_code = 200
        headers = {"x-ratelimit-remaining": "999"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [], "hasMore": False}

    class _Session:
        def get(self, url, **kw):
            calls.append(kw["params"]["cabins"])
            return _Resp()

    monkeypatch.setattr(award_scanner.seats_aero, "_get_api_key", lambda: "k")
    cfg = {"origins": ["JFK"], "destinations": ["LIS"], "cabins": ["ECONOMY", "BUSNIESS"], "sources": []}
    cands, stats = award_scanner.scan(cfg, today=date.today(), session=_Session())
    assert cands == [] and all(c == "economy" for c in calls)


def test_quote_save_prunes_old_and_past_quotes(tmp_path, monkeypatch):
    import cash_quotes
    monkeypatch.setattr(cash_quotes, "QUOTES_PATH", str(tmp_path / "q.json"))
    fresh_day = (date.today() + timedelta(days=30)).isoformat()
    old_fetch = (date.today() - timedelta(days=40)).strftime("%Y-%m-%dT00:00:00Z")
    new_fetch = date.today().strftime("%Y-%m-%dT00:00:00Z")
    rows = [
        {"origin": "JFK", "dest": "LIS", "cabin": "ECONOMY", "date": fresh_day, "fetched_at": new_fetch},
        {"origin": "JFK", "dest": "LIS", "cabin": "ECONOMY", "date": fresh_day, "fetched_at": old_fetch},
        {"origin": "JFK", "dest": "LIS", "cabin": "ECONOMY", "date": "2020-01-01", "fetched_at": new_fetch},
    ]
    cash_quotes.save(rows)
    kept = cash_quotes.load()
    assert len(kept) == 1 and kept[0]["fetched_at"] == new_fetch


def test_scan_returns_partial_results_when_the_daily_quota_runs_out(monkeypatch):
    """Losing a whole run to a 429 on the last program wastes the ~200 calls already spent."""
    import award_scanner

    class _Resp:
        def __init__(self, status, rows):
            self.status_code = status
            self.headers = {"x-ratelimit-remaining": "0"}
            self._rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self._rows, "hasMore": False}

    row = {"ID": "a", "Source": "aeroplan", "Date": "2027-01-10", "UpdatedAt": "2099-01-01T00:00:00Z",
           "TaxesCurrency": "USD", "YAvailable": True, "YMileageCostRaw": 30000, "YTotalTaxesRaw": 500,
           "YRemainingSeatsRaw": 2, "YDirectRaw": True, "YAirlinesRaw": "AC",
           "Route": {"OriginAirport": "JFK", "DestinationAirport": "LIS", "Distance": 3400}}
    calls = []

    class _Session:
        def get(self, url, **kw):
            calls.append(kw["params"]["sources"])
            return _Resp(200, [row]) if len(calls) == 1 else _Resp(429, [])

    monkeypatch.setattr(award_scanner.seats_aero, "_get_api_key", lambda: "k")
    cfg = {"origins": ["JFK"], "destinations": ["LIS"], "cabins": ["ECONOMY"], "sources": []}
    cands, stats = award_scanner.scan(cfg, today=date.today(), session=_Session())
    assert stats["quota_exhausted"] is True
    assert len(cands) == 1 and stats["candidates"] == 1  # keeps what it already scanned


# ── search(): one route over a date window ────────────────────────────────
import types

import pytest


def _cand(source, dest, points, date_str, origin="JFK"):
    import award_scanner
    return award_scanner.AwardCandidate(id=f"{source}{date_str}{points}", source=source, program=source,
                                        origin=origin, dest=dest, date=date_str, cabin="BUSINESS",
                                        points=points, taxes=50.0, taxes_currency="USD", seats=2, direct=True,
                                        airlines="AC", distance=3000, updated_at="")


def _scored(c, est_cpp=2.0, held=0):
    import fare_model
    s = deal_finder.Scored(c, 50.0, fare_model.Estimate(1500.0, 0.3, 0, "model"), 2.0, est_cpp, 0.05)
    s.held_miles = held
    return s


@pytest.fixture
def pipeline(monkeypatch):
    """Fakes for every step search() delegates to; nothing reaches seats.aero or Google."""
    state = types.SimpleNamespace(scored=[], cands=["x"], stats={"calls": 7}, config=None, priced_input=None,
                                  cash={}, rt=[], trips=None, trip_for={}, max_lookups=None)

    def scan(config, extra_sources=None, per_source=True):
        state.config, state.per_source = config, per_source
        return state.cands, state.stats

    def price(ordered, max_lookups, log=None):
        state.priced_input, state.max_lookups = list(ordered), max_lookups
        for s in ordered:
            if s.c.id in state.cash:
                s.cash = state.cash[s.c.id]
                s.cpp = (s.cash - s.taxes_usd) / s.c.points * 100

    def attach(deals, cache, budget):
        state.trips = (len(deals), budget)
        for s in deals:
            if s.c.id in state.trip_for:
                s.trip = state.trip_for[s.c.id]

    monkeypatch.setattr(route_search.ledger, "load_program_balances", lambda: {})
    monkeypatch.setattr(route_search.award_scanner, "transferable_sources", lambda: {"aeroplan": ["chase_ur"]})
    monkeypatch.setattr(route_search.award_scanner, "sources_for_programs", lambda b: [])
    monkeypatch.setattr(route_search.award_scanner, "scan", scan)
    monkeypatch.setattr(route_search.fare_model, "FareModel", lambda: None)
    monkeypatch.setattr(route_search.deal_finder, "score_candidates", lambda c, m, w, b: list(state.scored))
    monkeypatch.setattr(route_search.deal_finder, "price_promising", price)
    monkeypatch.setattr(route_search.deal_finder, "apply_round_trip", lambda s, cache: state.rt.append(s.c.id))
    monkeypatch.setattr(route_search.deal_finder, "attach_trips", attach)
    return state


def _days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_search_window_outside_bookable_range_never_scans(pipeline):
    past = date.today() - timedelta(days=10)
    r = route_search.search("JFK", "LIS", ["BUSINESS"], past - timedelta(days=5), past)
    assert r.deals == [] and r.calls == 0
    assert "outside the bookable window" in r.notes[0]
    assert pipeline.config is None  # no seats.aero call spent


def test_search_window_is_clipped_to_tomorrow_and_the_lookahead(pipeline):
    route_search.search("jfk", "lis", ["BUSINESS"], date.today() - timedelta(days=3),
                        date.today() + timedelta(days=900))
    cfg = pipeline.config
    assert cfg["start_date"] == _days(1)
    assert cfg["end_date"] == _days(330)
    assert cfg["origins"] == ["JFK"] and cfg["destinations"] == ["LIS"]
    assert cfg["max_data_age_days"] == 10
    assert pipeline.per_source is False  # one route: one query per cabin, all programs


def test_search_keeps_only_awards_you_can_fund(pipeline):
    ok = _scored(_cand("aeroplan", "LIS", 60000, _days(40)))
    held = _scored(_cand("delta", "LIS", 50000, _days(41)), held=50000)
    short = _scored(_cand("delta", "LIS", 40000, _days(42)), held=39999)  # not transferable, too few miles
    pipeline.scored = [ok, held, short]
    pipeline.cash = {c.c.id: 2050.0 for c in (ok, held, short)}
    r = route_search.search("JFK", "LIS", ["BUSINESS"], max_lookups=3)
    assert r.awards_found == 2
    assert {d.c.source for d in r.deals} == {"aeroplan", "delta"}
    assert short not in pipeline.priced_input
    assert pipeline.max_lookups == 3


def test_search_prices_best_estimates_first_even_below_the_bar(pipeline):
    lo = _scored(_cand("aeroplan", "LIS", 60000, _days(40)), est_cpp=1.1)
    hi = _scored(_cand("aeroplan", "LIS", 70000, _days(41)), est_cpp=3.4)
    pipeline.scored = [lo, hi]
    route_search.search("JFK", "LIS", ["BUSINESS"])
    assert pipeline.priced_input == [hi, lo]  # by estimated CPP, not dollar surplus
    assert lo.p_great == 0.2 and hi.p_great == 0.2  # raised from 0.05 so they get priced


def test_search_folds_same_award_on_other_dates_into_one_deal(pipeline):
    a = _scored(_cand("aeroplan", "LIS", 60000, _days(50)))
    b = _scored(_cand("aeroplan", "LIS", 60000, _days(45)))
    c = _scored(_cand("aeroplan", "LIS", 60000, _days(48)))
    other = _scored(_cand("aeroplan", "LIS", 75000, _days(45)))  # different points: its own deal
    pipeline.scored = [a, b, c, other]
    pipeline.cash = {a.c.id: 2450.0, b.c.id: 2050.0, c.c.id: 2250.0, other.c.id: 2300.0}
    r = route_search.search("JFK", "LIS", ["BUSINESS"])
    assert [d.c.id for d in r.deals] == [a.c.id, other.c.id]  # 4.0c, then 3.0c
    lead = r.deals[0]  # the best date leads; the others become its other dates
    assert lead.other_dates == sorted([_days(45), _days(48)])
    assert r.priced == 4


def test_search_round_trip_checks_only_the_leaders_and_ranks_by_cpp(pipeline):
    deals = [_scored(_cand("aeroplan", "LIS", 10000 + i * 1000, _days(40 + i))) for i in range(10)]
    pipeline.scored = deals
    pipeline.cash = {d.c.id: 1050.0 for d in deals}  # lower points => higher CPP
    r = route_search.search("JFK", "LIS", ["BUSINESS"], top=5)
    assert pipeline.rt == [d.c.id for d in deals[:8]]  # ROUND_TRIP_CHECKS = 8
    assert [d.c.points for d in r.deals] == [10000, 11000, 12000, 13000, 14000]
    assert pipeline.trips == (5, deal_finder.INTERACTIVE_TRIP_LOOKUPS)


def test_search_lists_mixed_cabin_awards_after_full_cabin_ones(pipeline):
    mixed = _scored(_cand("aeroplan", "LIS", 40000, _days(40)))
    full = _scored(_cand("aeroplan", "LIS", 60000, _days(41)))
    pipeline.scored = [mixed, full]
    pipeline.cash = {mixed.c.id: 2050.0, full.c.id: 2050.0}  # mixed has the better CPP
    pipeline.trip_for = {mixed.c.id: types.SimpleNamespace(mixed_cabin=True),
                         full.c.id: types.SimpleNamespace(mixed_cabin=False)}
    r = route_search.search("JFK", "LIS", ["BUSINESS"])
    assert [d.c.id for d in r.deals] == [full.c.id, mixed.c.id]


def test_search_notes_empty_and_truncated_scans(pipeline):
    pipeline.cands, pipeline.stats = [], {"calls": 3, "truncated": ["aeroplan/BUSINESS"]}
    r = route_search.search("JFK", "LIS", ["BUSINESS"])
    assert r.calls == 3 and r.deals == []
    assert r.notes == ["Very large result set; some awards may be missing.",
                       "seats.aero has no award space for this route in your programs and dates."]


def test_pair_values_against_the_round_trip_fare_when_cheaper(monkeypatch):
    out = route_search.RouteResult([_deal("LIS", "JFK", 60000, 2000, "2027-01-10")], 1, 1, 1)
    back = route_search.RouteResult([_deal("JFK", "LIS", 60000, 1800, "2027-01-20")], 1, 1, 1)
    monkeypatch.setattr(route_search, "search", lambda *a, **k: out if a[1] != "JFK" else back)
    asked = []

    def rt(o, d, dep, ret, cabin):
        asked.append((o, d, dep, ret, cabin))
        return [types.SimpleNamespace(price_usd=2900.0), types.SimpleNamespace(price_usd=3100.0)]

    monkeypatch.setattr(route_search.flight_search, "search_round_trip_offers", rt)
    r = route_search.search_pair("JFK", "LIS", ["BUSINESS"], date(2027, 1, 1), date(2027, 1, 15),
                                 date(2027, 1, 15), date(2027, 1, 30))
    assert asked == [("JFK", "LIS", "2027-01-10", "2027-01-20", "BUSINESS")]
    assert r.pair_cash_one_ways == 3800.0
    assert r.pair_round_trip_fare == 2900.0
    # (2900 - 100 taxes) / 120,000 points
    assert r.combined_cpp == pytest.approx(2.3333, abs=1e-4)


def test_pair_keeps_one_way_total_when_round_trip_is_dearer_or_empty(monkeypatch):
    out = route_search.RouteResult([_deal("LIS", "JFK", 60000, 2000, "2027-01-10")], 1, 1, 1)
    back = route_search.RouteResult([_deal("JFK", "LIS", 60000, 1800, "2027-01-20")], 1, 1, 1)
    monkeypatch.setattr(route_search, "search", lambda *a, **k: out if a[1] != "JFK" else back)
    monkeypatch.setattr(route_search.flight_search, "search_round_trip_offers", lambda *a: [])
    r = route_search.search_pair("JFK", "LIS", ["BUSINESS"], date(2027, 1, 1), date(2027, 1, 15),
                                 date(2027, 1, 15), date(2027, 1, 30))
    assert r.pair_round_trip_fare is None
    assert r.combined_cpp == pytest.approx(3.0833, abs=1e-4)  # (3800 - 100 taxes) / 120,000 = 3.083c
