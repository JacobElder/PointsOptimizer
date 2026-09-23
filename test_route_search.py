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
