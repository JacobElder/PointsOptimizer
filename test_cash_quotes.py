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
