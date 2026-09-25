"""One test that runs deal_finder.run() start to finish with fake providers.

Every earlier bug in this pipeline (the round-trip valuation being overwritten,
unverified deals reaching the top 20) survived because the pieces were only ever
tested in isolation.
"""

import json
from datetime import date, timedelta

import award_scanner
import award_trips
import cash_quotes
import deal_finder


def _award(dest, cabin, source, points, day_offset, seats=2):
    return award_scanner.AwardCandidate(
        id=f"{source}{dest}{cabin}{points}", source=source,
        program=award_scanner.seats_aero.SOURCE_TO_PARTNER.get(source, source),
        origin="JFK", dest=dest, date=(date.today() + timedelta(days=day_offset)).isoformat(),
        cabin=cabin, points=points, taxes=50.0, taxes_currency="USD", seats=seats, direct=False,
        airlines="AC", distance=4000, updated_at="2099-01-01T00:00:00Z")


def _fake_pipeline(tmp_path, monkeypatch, awards):
    """Wire run() to fake providers: no network, no seats.aero quota spent."""
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(tmp_path / "digest.json"))
    monkeypatch.setattr(deal_finder.award_history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(cash_quotes, "QUOTES_PATH", str(tmp_path / "quotes.json"))
    monkeypatch.setattr(award_scanner, "scan", lambda *a, **k: (awards, {
        "calls": 12, "rows": len(awards), "stale": 0, "sources": ["aeroplan", "flyingblue"],
        "rate_limit_remaining": "900", "candidates": len(awards), "truncated": [],
        "quota_exhausted": False, "per_source": {"aeroplan": 3, "flyingblue": 1}}))
    monkeypatch.setattr(award_scanner, "remaining_calls", lambda *a, **k: 900)
    monkeypatch.setattr(deal_finder.ledger, "load_program_balances", lambda: {})
    monkeypatch.setattr(deal_finder.ledger, "load_balances", lambda: {"chase_ur": 200000})
    monkeypatch.setattr(deal_finder, "load_watchlist", lambda config: [])

    def _fetch_offers(origin, dest, day, cabin, max_results=100):
        seg = deal_finder.flight_search.FlightSegment(
            airline="Air Canada", flight_number="AC1", dep_airport=origin, dep_airport_name=origin,
            dep_time=f"{day} 10:00", arr_airport=dest, arr_airport_name=dest, arr_time=f"{day} 20:00",
            duration_minutes=600, airline_code="AC")
        return [deal_finder.flight_search.FlightOffer(price_usd=3600.0, cabin=cabin,
                                                     total_duration_minutes=600, segments=[seg],
                                                     layovers=[], provider="test")]

    monkeypatch.setattr(deal_finder.flight_search, "search_cash_price", _fetch_offers)
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers",
                        lambda o, d, dep, ret, cabin: _fetch_offers(o, d, dep, cabin))
    monkeypatch.setattr(award_trips, "fetch", lambda aid, cabin, points, **k: award_trips.TripInfo(
        flights=["AC1 JFK–ZRH"], connections=[], duration_min=600, departs_at="", arrives_at="",
        leg_cabins=[cabin.lower()], mixed_cabin=False, lower_cabin_legs=[], carriers="Air Canada",
        booking_url="https://example.com", booking_label="Book", other_itineraries=0,
        airport_changes=[], stops=0, nonstop=True, seats=2, current_points=points,
        price_matches=True))


def test_resend_reports_again_without_wiping_the_email_history(tmp_path, monkeypatch):
    """--resend must ignore the 14-day cooldown for THIS email without clearing the
    history: wiping it would re-email every deal again on the next normal run."""
    award = _award("ZRH", "BUSINESS", "aeroplan", 60000, 60)
    _fake_pipeline(tmp_path, monkeypatch, [award])
    monkeypatch.setattr(deal_finder, "_load_digest", lambda: {
        "reported": {"old|JFK|LIS|ECONOMY|20000|2027-02-02": "2099-01-01T00:00:00"}})

    out = deal_finder.run(max_lookups=10, top=5, send_email=False, resend=True, log=lambda m: None)

    zrh = next(d for d in out["top"] if d["dest"] == "ZRH")
    assert zrh["new"] is True  # resend ignores the cooldown
    assert "old|JFK|LIS|ECONOMY|20000|2027-02-02" in out["reported"]  # history survives


def test_full_run_produces_a_sane_digest(tmp_path, monkeypatch):
    awards = [
        _award("ZRH", "BUSINESS", "aeroplan", 60000, 60),      # strong deal
        _award("JFK", "BUSINESS", "aeroplan", 65000, 70),      # its return leg (dest JFK)
        _award("LIS", "ECONOMY", "flyingblue", 20000, 90),     # economy deal
        _award("CDG", "BUSINESS", "aeroplan", 300000, 80),     # priced but far below bar
    ]
    awards[1].origin, awards[1].dest = "ZRH", "JFK"

    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(tmp_path / "digest.json"))
    monkeypatch.setattr(deal_finder.award_history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(cash_quotes, "QUOTES_PATH", str(tmp_path / "quotes.json"))
    monkeypatch.setattr(award_scanner, "scan", lambda *a, **k: (awards, {
        "calls": 12, "rows": len(awards), "stale": 0, "sources": ["aeroplan", "flyingblue"],
        "rate_limit_remaining": "900", "candidates": len(awards), "truncated": [],
        "quota_exhausted": False, "per_source": {"aeroplan": 3, "flyingblue": 1}}))
    monkeypatch.setattr(award_scanner, "remaining_calls", lambda *a, **k: 900)
    monkeypatch.setattr(deal_finder.ledger, "load_program_balances", lambda: {})
    monkeypatch.setattr(deal_finder.ledger, "load_balances", lambda: {"chase_ur": 200000})

    fares = {"ZRH": 3600.0, "LIS": 1400.0, "CDG": 2500.0, "JFK": 3400.0}

    def _fetch_offers(origin, dest, day, cabin, max_results=100):
        price = fares.get(dest, 1000.0)
        seg = deal_finder.flight_search.FlightSegment(
            airline="Air Canada", flight_number="AC1", dep_airport=origin, dep_airport_name=origin,
            dep_time=f"{day} 10:00", arr_airport=dest, arr_airport_name=dest, arr_time=f"{day} 20:00",
            duration_minutes=600, airline_code="AC")
        return [deal_finder.flight_search.FlightOffer(price_usd=price, cabin=cabin,
                                                      total_duration_minutes=600, segments=[seg],
                                                      layovers=[], provider="test")]

    monkeypatch.setattr(deal_finder.flight_search, "search_cash_price", _fetch_offers)
    monkeypatch.setattr(deal_finder.flight_search, "search_round_trip_offers",
                        lambda o, d, dep, ret, cabin: _fetch_offers(o, d, dep, cabin) * 1)
    monkeypatch.setattr(award_trips, "fetch", lambda aid, cabin, points, **k: award_trips.TripInfo(
        flights=["AC1 JFK–ZRH"], connections=[], duration_min=600, departs_at="2027-01-01T10:00:00Z",
        arrives_at="2027-01-01T20:00:00Z", leg_cabins=[cabin.lower()], mixed_cabin=False,
        lower_cabin_legs=[], carriers="Air Canada", booking_url="https://example.com",
        booking_label="Book", other_itineraries=0, airport_changes=[], stops=0, nonstop=True,
        seats=2, current_points=points, price_matches=True))

    # A watchlist entry covering the same destination, so the watchlist section is
    # exercised too, not just the general top list.
    monkeypatch.setattr(deal_finder, "load_watchlist",
                        lambda config: [deal_finder.WatchEntry(label="Switzerland", dests=["ZRH"])])

    out = deal_finder.run(max_lookups=10, top=5, send_email=False, log=lambda m: None)

    assert out["top"], "a strong deal should be reported"
    # One award, one card: ZRH is claimed by the watchlist, so the general top list
    # doesn't repeat it on another date at another value.
    watch_deals = [d for g in out["watchlist"] for d in g["deals"]]
    assert [d["dest"] for d in watch_deals] == ["ZRH"]
    assert all(d["dest"] != "ZRH" for d in out["top"])

    zrh = watch_deals[0]
    # Round-trip half (1800) beats the one-way (3600) and survives verification.
    assert zrh["cash_price"] == 1800.0 and "round trip" in zrh["cash_basis"]
    assert zrh["cpp"] == pytest.approx((1800.0 - 50.0) / 60000 * 100, abs=0.01)  # digest rounds to 3dp
    assert zrh["trip"] is not None and zrh["trip"]["booking_url"]
    assert zrh["unverified"] is False and zrh["watch_bar"]
    # The return leg from the same scan is paired in, and watchlist cards are
    # serialized late enough to carry it.
    assert zrh["return_option"]["points"] == 65000
    # Balances never reach the public digest.
    assert not any(k in zrh for k in ("pay_summary", "held_miles", "bookable_now"))
    # A 300k-point award is far below its bar and must not be reported.
    assert all(d["dest"] != "CDG" for d in out["top"] + watch_deals)

    # The digest is valid JSON on disk with the history recorded alongside.
    assert json.load(open(deal_finder.DIGEST_PATH))["top"]
    assert json.load(open(deal_finder.award_history.HISTORY_PATH))


import pytest  # noqa: E402  (kept last so the module reads top-down)
