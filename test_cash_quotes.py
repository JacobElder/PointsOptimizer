import json

import cash_quotes
import flight_search
from cash_quotes import Quote, comparable_fare


def _q(offers, price=None, nonstop=None):
    return Quote("JFK", "ZRH", "BUSINESS", "2026-11-12", price if price is not None else min(o[0] for o in offers),
                 nonstop, "test", "2026-09-16T00:00:00Z", offers=offers)


OFFERS = [[1468.0, 1, "FI,FI"], [1792.0, 1, "JU,JU"], [4169.0, 0, "LX"], [6069.0, 0, "DL"], [4188.0, 1, "AF,AF"]]


def test_nonstop_award_compares_against_cheapest_fare_with_at_most_one_stop():
    offers = OFFERS + [[900.0, 2, "UA,LH,OS"]]
    c = comparable_fare(_q(offers), direct=True, carriers="LX")
    assert (c.price, c.same_carrier_price, c.nonstop_price) == (1468.0, 4169.0, 4169.0)
    assert "at most 1 stop" in c.basis


def test_connecting_award_compares_against_cheapest_overall_and_reports_own_airline():
    c = comparable_fare(_q(OFFERS + [[900.0, 2, "UA,LH"]]), direct=False, carriers="AF, KL")
    assert (c.price, c.same_carrier_price) == (900.0, 4188.0)


def test_nonstop_award_with_only_multi_stop_fares_falls_back_to_cheapest():
    assert comparable_fare(_q([[900.0, 2, "UA"]]), direct=True).price == 900.0


def test_legacy_quote_without_offers_uses_cheapest_price():
    legacy = Quote("JFK", "ZRH", "BUSINESS", "2026-11-12", 1468.0, 4169.0, "fast-flights", "2026-09-16T00:00:00Z")
    c = comparable_fare(legacy, direct=True)
    assert (c.price, c.nonstop_price) == (1468.0, 4169.0)


def _payload_html(best, other):
    def row(price, segs):
        return [["XX", ["Air"], [
            [None, None, None, o, "O", "D", d, None, [10, 0], None, [18, 0], 480] + [None] * 5 + ["A320"] + [None] * 2
            + [[2026, 11, 12], [2026, 11, 12], [code, num, None, "Air"]]
            for (o, d, code, num) in segs]], [[None, price]]]
    payload = [None, None, [[row(*r) for r in best]], [[row(*r) for r in other]]]
    return ('<script class="ds:1">AF_initDataCallback({key: "ds:1", data:'
            + json.dumps(payload) + ', sideChannel: {}});</script>')


def test_parser_reads_best_and_other_lists_and_skips_priceless_rows():
    html = _payload_html(best=[(4169, [("JFK", "ZRH", "LX", "17")])],
                         other=[(1468, [("JFK", "KEF", "FI", "614"), ("KEF", "ZRH", "FI", "568")])])
    offers = flight_search._parse_google_flights_html(html, "BUSINESS")
    assert [o.price_usd for o in offers] == [1468.0, 4169.0]
    assert offers[1].stops == 0 and offers[1].carrier_codes == ["LX"]
    assert offers[0].segments[0].flight_number == "FI 614"

    broken = html.replace("[[null, 1468]]", "[]")
    assert [o.price_usd for o in flight_search._parse_google_flights_html(broken, "BUSINESS")] == [4169.0]


def _stored(date, price, fetched):
    return {"origin": "EWR", "dest": "CPT", "cabin": "ECONOMY", "date": date, "price_usd": price,
            "nonstop_price_usd": None, "provider": "fast-flights", "fetched_at": fetched, "offers": []}


def test_empty_answer_from_yesterday_does_not_block_pricing(monkeypatch):
    # Oct 10: Google throttled the runner for three minutes and 98 lookups came back
    # empty. Saved as "no fare", each blocked its exact date for up to a week, even
    # where a real fare a day away was sitting in the store.
    from datetime import datetime, timezone
    monkeypatch.setattr(cash_quotes, "_now", lambda: datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc))
    quotes = [_stored("2026-11-04", None, "2026-10-10T14:34:00Z"),
              _stored("2026-11-05", 1210.0, "2026-10-09T15:20:00Z")]
    q = cash_quotes.find_cached(quotes, "EWR", "CPT", "2026-11-04", "ECONOMY")
    assert q is not None and q.price_usd == 1210.0 and q.approx

    # With nothing nearby, it is looked up again rather than trusted.
    assert cash_quotes.find_cached(quotes[:1], "EWR", "CPT", "2026-11-04", "ECONOMY") is None


def test_empty_answer_within_the_same_run_is_not_retried(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(cash_quotes, "_now", lambda: datetime(2026, 10, 10, 14, 50, tzinfo=timezone.utc))
    quotes = [_stored("2026-11-04", None, "2026-10-10T14:34:00Z")]
    q = cash_quotes.find_cached(quotes, "EWR", "CPT", "2026-11-04", "ECONOMY")
    assert q is not None and q.price_usd is None


EMPTY_PAGE = '<script class="ds:1">AF_initDataCallback({key: "ds:1", data:[null, null, null, null], sideChannel: {}});</script>'


def _google_stub(monkeypatch, pages):
    """Feed fast-flights lookups from `pages` (a page, or an exception to raise) and record pauses."""
    sleeps, pages = [], list(pages)

    def fetch(_query):
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(flight_search, "_fetch_html", fetch)
    monkeypatch.setattr(flight_search, "_sleep", sleeps.append)
    monkeypatch.setattr(flight_search, "fast_flights_available", lambda: True)
    monkeypatch.setattr(flight_search, "_backoff", dict(flight_search._backoff))  # restored after the test
    monkeypatch.setattr(flight_search, "_lookups", [])
    flight_search.reset_backoff(enabled=True)
    return sleeps


def test_empty_answers_slow_the_next_lookups_down_and_a_real_one_resets(monkeypatch):
    # Oct 10-11: Google throttled the runner, and the empty answers came in the
    # busiest minute (Oct 11 15:02: 78 lookups, 20 empty) while lookups ran back
    # to back. The 2 s-spaced end-of-run retry recovered 8 of 10.
    full = _payload_html(best=[(4169, [("JFK", "ZRH", "LX", "17")])], other=[])
    sleeps = _google_stub(monkeypatch, [EMPTY_PAGE, EMPTY_PAGE, RuntimeError("blocked"), full, full])
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-12", "BUSINESS")
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-13", "BUSINESS")
    try:
        flight_search.search_round_trip_offers("JFK", "ZRH", "2026-11-14", "2026-11-21", "BUSINESS")
    except flight_search.SearchFailed:
        pass
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-15", "BUSINESS")
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-16", "BUSINESS")
    assert sleeps == [3.0, 6.0, 12.0]
    log = flight_search.lookup_log()
    assert [(kind, ok) for _, kind, ok in log] == [
        ("one-way", False), ("one-way", False), ("round-trip", False), ("one-way", True), ("one-way", True)]


def test_pauses_stop_growing_and_stop_altogether_past_the_run_total(monkeypatch):
    sleeps = _google_stub(monkeypatch, [EMPTY_PAGE] * 40)
    for _ in range(40):
        flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-12", "BUSINESS")
    assert max(sleeps) == 24.0
    assert sum(sleeps) <= 240.0
    assert sum(sleeps) >= 230.0  # it does use the allowance before giving up


def test_no_pauses_unless_the_run_turns_them_on(monkeypatch):
    sleeps = _google_stub(monkeypatch, [EMPTY_PAGE, EMPTY_PAGE])
    flight_search.reset_backoff(enabled=False)
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-12", "BUSINESS")
    flight_search._fetch_offers_fast_flights("JFK", "ZRH", "2026-11-13", "BUSINESS")
    assert sleeps == []


# ── Window checks, reuse tiers, save safety ───────────────────────────────
from datetime import date, datetime, timezone

import pytest


def test_quote_with_no_fare_has_no_comparable():
    q = Quote("JFK", "ZRH", "BUSINESS", "2026-11-12", None, None, "fast-flights", "2026-09-16T00:00:00Z")
    assert comparable_fare(q, direct=True) is None


def test_check_window_rejects_past_and_beyond_google_window():
    today = date(2026, 10, 23)
    cash_quotes.check_window("2026-10-23", today)  # today is fine
    cash_quotes.check_window("2027-09-18", today)  # 330 days out: still listed
    with pytest.raises(cash_quotes.OutOfWindow, match="in the past"):
        cash_quotes.check_window("2026-10-22", today)
    with pytest.raises(cash_quotes.OutOfWindow, match="331 days out"):
        cash_quotes.check_window("2027-09-19", today)


def test_reuse_tiers_tighten_as_the_trip_nears():
    today = date(2026, 10, 23)
    assert cash_quotes._reuse_limits(date(2026, 12, 7), today) == (2, 3)  # 45 days out
    assert cash_quotes._reuse_limits(date(2026, 12, 8), today) == (7, 7)  # 46
    assert cash_quotes._reuse_limits(date(2027, 2, 20), today) == (7, 7)  # 120
    assert cash_quotes._reuse_limits(date(2027, 2, 21), today) == (14, 14)  # 121


def test_reuse_falls_back_to_the_approx_limits_past_every_tier(monkeypatch):
    monkeypatch.setattr(cash_quotes, "REUSE_TIERS", ((45, 2, 3),))
    assert cash_quotes._reuse_limits(date(2027, 3, 1), date(2026, 10, 23)) == (7, 14)


def test_exact_date_quote_age_limit_is_2_days_close_in_and_7_further_out(monkeypatch):
    monkeypatch.setattr(cash_quotes, "_now", lambda: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
    near = [_stored("2026-11-20", 500.0, "2026-10-21T11:00:00Z")]  # 28 days out, 2.04 days old
    assert cash_quotes.find_cached(near, "EWR", "CPT", "2026-11-20", "ECONOMY", allow_approx=False) is None
    far = [_stored("2027-01-20", 500.0, "2026-10-17T11:00:00Z")]  # 6.04 days old
    assert cash_quotes.find_cached(far, "EWR", "CPT", "2027-01-20", "ECONOMY", allow_approx=False).price_usd == 500.0
    # max_age_days tightens it further (the fresh re-pricing before publishing).
    assert cash_quotes.find_cached(far, "EWR", "CPT", "2027-01-20", "ECONOMY", allow_approx=False,
                                   max_age_days=0.25) is None


def test_malformed_dates_in_the_store_are_ignored(monkeypatch):
    monkeypatch.setattr(cash_quotes, "_now", lambda: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
    quotes = [_stored("11/20/2026", 400.0, "2026-10-23T11:00:00Z"), _stored("2027-01-20", 500.0, "2026-10-23T11:00:00Z")]
    assert cash_quotes.find_cached(quotes, "ewr", "cpt", "2027-01-20", "economy").price_usd == 500.0


def test_get_quote_offline_returns_none_without_a_lookup(monkeypatch):
    monkeypatch.setattr(flight_search, "search_cash_price",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no live lookup")))
    assert cash_quotes.get_quote("EWR", "CPT", "2027-01-20", "ECONOMY", allow_live=False) is None


def test_get_quote_never_looks_up_outside_the_window(monkeypatch):
    monkeypatch.setattr(flight_search, "search_cash_price",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no live lookup")))
    with pytest.raises(cash_quotes.OutOfWindow):
        cash_quotes.get_quote("EWR", "CPT", "2020-01-01", "ECONOMY")


def test_get_quote_saves_live_offers_and_reuses_them(monkeypatch):
    from datetime import timedelta
    day = (date.today() + timedelta(days=60)).isoformat()

    def offer(price, codes):
        segs = [flight_search.FlightSegment("A", f"{c} 1", "EWR", "", "", "CPT", "", "", 60, c) for c in codes]
        lay = [flight_search.Layover("X", "", 60)] * (len(codes) - 1)
        return flight_search.FlightOffer(price, "ECONOMY", 0, segs, lay)

    calls = []

    def search(o, d, dt, cabin, max_results):
        calls.append((o, d, dt, cabin, max_results))
        return [offer(900.0, ["ET", "ET"]), offer(1500.0, ["UA"])]

    monkeypatch.setattr(flight_search, "search_cash_price", search)
    q = cash_quotes.get_quote(" ewr", "cpt ", day, "economy")
    assert (q.price_usd, q.nonstop_price_usd, q.offers) == (900.0, 1500.0, [[900.0, 1, "ET,ET"], [1500.0, 0, "UA"]])
    assert calls == [("EWR", "CPT", day, "ECONOMY", 100)]
    again = cash_quotes.get_quote("EWR", "CPT", day, "ECONOMY")
    assert again.price_usd == 900.0 and again.approx is False and len(calls) == 1


def test_failed_save_leaves_the_old_store_and_no_temp_file(tmp_path, monkeypatch):
    import os
    from datetime import timedelta
    day = (date.today() + timedelta(days=60)).isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cash_quotes.save([_stored(day, 700.0, now)])

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cash_quotes.os, "replace", boom)
    with pytest.raises(RuntimeError):
        cash_quotes.save([])
    assert [q["price_usd"] for q in cash_quotes.load()] == [700.0]
    assert [p for p in os.listdir(os.path.dirname(cash_quotes.QUOTES_PATH)) if p.startswith(".cash_quotes-")] == []
