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
    assert w.matches(c) and w.bar_for("BUSINESS") == 1.8
    assert not w.matches(_scored("NRT", "BUSINESS", "aeroplan", "JFK", 75000, 3000, date="2027-05-01").c)
    assert not w.matches(_scored("NRT", "ECONOMY", "aeroplan", "JFK", 35000, 900, date="2027-03-25").c)
    assert deal_finder.WatchEntry.from_config({"dests": ["LIS"]}).bar_for("ECONOMY") == 1.5


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


def test_round_trip_half_replaces_higher_one_way_fare(monkeypatch):
    s = _scored("CPT", "BUSINESS", "united", "EWR", 88000, 5084, date="2027-02-09")
    s.one_way_cash = 5084.0
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers",
                        lambda *a, **k: [_RT(5038.0, 1), _RT(3000.0, 3)])
    s.c.direct = True
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
