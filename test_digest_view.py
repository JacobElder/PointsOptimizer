"""Site cards (digest_view): every line the user reads before transferring points.

Streamlit calls are replaced with a recorder so each test can assert the exact
text a card prints, including that "$" is escaped (Streamlit renders $..$ as LaTeX).
"""

import contextlib
import copy
import json
import os

import pytest

import deal_finder
import digest_view


class FakeSt:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def _rec(self, kind):
        def f(text="", *a, **k):
            self.calls.append((kind, str(text)))
        return f

    def __getattr__(self, name):
        if name in ("info", "header", "subheader", "caption", "markdown", "write", "warning"):
            return self._rec(name)
        raise AttributeError(name)

    @contextlib.contextmanager
    def container(self, **k):
        yield

    def columns(self, spec, **k):
        return [contextlib.nullcontext() for _ in spec]

    def texts(self, kind=None):
        return [t for k, t in self.calls if kind is None or k == kind]

    @property
    def all(self) -> str:
        return "\n".join(self.texts())


@pytest.fixture
def st(monkeypatch):
    fake = FakeSt()
    monkeypatch.setattr(digest_view, "st", fake)
    monkeypatch.setattr(digest_view, "_balances", lambda: ({}, {}))
    return fake


def _card() -> dict:
    """A card as the digest stores it (shape of deal_digest.json's top[0])."""
    return copy.deepcopy({
        "origin": "EWR", "dest": "TLV", "program": "Air Canada Aeroplan", "cabin": "BUSINESS",
        "date": "2027-01-21", "points": 62400, "taxes_usd": 79.81, "cash_price": 2488.5,
        "cash_basis": "half of a 7-night round trip (the one-way fare is $2,646)",
        "cash_is_approx": False, "one_way_cash": 2646.0, "round_trip_half": 2488.5,
        "cpp": 3.86, "great_floor": 2.625, "baseline_cpp": 2.1, "surplus_usd": 1098.0,
        "airlines": "AC, H3, LH, LO, OS", "age_days": 0.3, "age_shown": 0, "new": False,
        "trip": {"flights": ["LO16 EWR–WAW", "H3155 WAW–TLV"], "connections": ["WAW"],
                 "duration_min": 850, "departs_at": "2027-01-21T22:35:00Z",
                 "arrives_at": "2027-01-22T19:45:00Z", "mixed_cabin": False, "lower_cabin_legs": [],
                 "carriers": "THREE, LOT Polish", "booking_url": "https://example.com/book",
                 "booking_label": "Book via Air Canada Aeroplan", "airport_changes": [],
                 "stops": 1, "seats": 1},
        "slow": False, "rt_unavailable": False, "unverified": False, "trip_unverified": False,
        "taxes_unknown": False, "taxes_estimated": False, "rank_notes": [],
        "returns_checked": True, "typical_fare": 2657, "cpp_at_typical": 3.86,
    })


def test_real_digest_card_renders_without_error(st):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "deal_digest.json")) as f:
        top = json.load(f)["top"]
    for d in top:
        digest_view.deal_card(d)
    assert not any(t.startswith("Couldn't show") for t in st.texts("warning"))


def test_headline_escapes_dollars_and_names_the_verdict(st):
    digest_view.deal_card(_card(), rank=2)
    md = st.texts("markdown")
    assert "#### 2. Newark (EWR) → Tel Aviv, Israel (TLV)" in md
    assert ("🟢 Book · **3.86¢ per point** · 62,400 points + \\$80 taxes · vs a \\$2,488 cash fare") in md
    assert ("**\\$1,098 better** than spending these points the usual way "
            "(about 2.10¢ each; we only flag Air Canada Aeroplan above 2.62¢)") in md
    # No unescaped "$" anywhere the markdown renderer sees it.
    for t in st.texts("markdown") + st.texts("caption") + st.texts("write"):
        assert "$" not in t.replace("\\$", ""), t


def test_borderline_and_skip_verdicts(st):
    d = _card()
    d["cpp"] = 2.2  # 0.84 of the 2.625 bar
    digest_view.deal_card(d)
    d["cpp"] = 2.0  # 0.76 of the bar
    digest_view.deal_card(d)
    d["cpp"] = None
    digest_view.deal_card(d)
    md = st.texts("markdown")
    assert any(t.startswith("🟡 Borderline · **2.20¢ per point**") for t in md)
    assert any(t.startswith("🔴 Skip · **2.00¢ per point**") for t in md)
    assert any(t.startswith("no value yet · 62,400 points") for t in md)


def test_taxes_wording(st):
    d = _card()
    d["taxes_estimated"] = True
    digest_view.deal_card(d)
    d["taxes_unknown"] = True
    digest_view.deal_card(d)
    md = st.all
    assert "62,400 points + about \\$80 taxes (estimated)" in md
    assert "62,400 points + taxes not reported" in md


def test_new_tag_in_caption(st):
    d = _card()
    d["new"] = True
    digest_view.deal_card(d)
    assert "Business · Air Canada Aeroplan · 🆕 new" in st.texts("caption")


def test_usual_fare_line_says_not_emailed_when_under_the_bar(st):
    d = _card()
    d.update(cash_price=3200.0, typical_fare=2400, cpp_at_typical=2.42)
    digest_view.deal_card(d)
    assert ("💲 At this route's usual \\$2,400 fare it's 2.42¢/pt, under the 2.62¢ bar: only worth it "
            "if you must fly this date (not emailed)") in st.texts("caption")


def test_usual_fare_line_above_bar_and_hidden_when_fare_is_normal(st):
    d = _card()
    d.update(cash_price=3200.0, typical_fare=2900, cpp_at_typical=3.0)
    digest_view.deal_card(d)
    assert "💲 At this route's usual \\$2,900 fare it's 3.00¢/pt" in st.texts("caption")
    st.calls.clear()
    digest_view.deal_card(_card())  # today's fare is below usual
    assert not any("usual" in t and "💲" in t for t in st.texts())


def test_price_history_and_rank_notes(st):
    d = _card()
    d.update(history_pct=0.995, history_days=28, rank_notes=["fare $ spike", "slow"])
    digest_view.deal_card(d)
    d.update(history_pct=0.91)
    digest_view.deal_card(d)
    d.update(history_pct=0.25)
    digest_view.deal_card(d)
    cap = st.texts("caption")
    assert "📉 Cheapest this route has been in the last 28 days" in cap
    assert "📉 Cheaper than 91% of the last 28 days on this route" in cap
    assert "📉 Pricier than usual: 75% of the last 28 days were cheaper" in cap
    assert "⚖️ Ranked lower because fare \\$ spike; slow" in cap


def test_same_carrier_and_one_way_fare_lines(st):
    d = _card()
    d.update(airlines="AC", same_carrier_cash=2000.0, same_carrier_code="AC")
    digest_view.deal_card(d)
    cap = st.texts("caption")
    assert "↘️ On Air Canada itself the fare is \\$2,000, which would make this 3.08¢/pt." in cap
    assert "The one-way fare on this date is \\$2,646." in cap
    assert "Cash fare: half of a 7-night round trip (the one-way fare is \\$2,646)" in cap


def test_same_carrier_line_names_the_airline_the_fare_is_on(st):
    d = _card()
    d.update(same_carrier_cash=2000.0, same_carrier_code="LO")  # airlines = "AC, H3, LH, LO, OS"
    digest_view.deal_card(d)
    assert any(t.startswith("↘️ On LOT Polish itself") for t in st.texts("caption"))


def test_cash_basis_notes(st):
    d = _card()
    d.update(cash_is_approx=True, cabin="FIRST", cash_basis=None)
    digest_view.deal_card(d)
    assert ("Cash fare: cheapest comparable fare (borrowed from a nearby date, not this one)"
            " · first class compared with the business fare") in st.texts("caption")


def test_flight_line_from_trip_details(st):
    digest_view.deal_card(_card())
    assert "Thu, Jan 21, 2027 · 10:35 PM → 7:45 PM (+1 day) · 14h 10m · 1 stop via Warsaw" in st.texts("write")
    cap = st.texts("caption")
    assert "LO16 EWR–WAW · H3155 WAW–TLV · THREE, LOT Polish" in cap
    assert "1 seat left when we checked" in cap


def test_nonstop_only_from_trip_stops(st):
    d = _card()
    d["trip"].update(stops=0, connections=[], seats=4, duration_min=45,
                     arrives_at="2027-01-21T23:20:00Z")
    digest_view.deal_card(d)
    assert "Thu, Jan 21, 2027 · 10:35 PM → 11:20 PM · 45m · Nonstop" in st.texts("write")
    assert "4 seats left when we checked" in st.texts("caption")


def test_two_stops_and_two_days_later(st):
    d = _card()
    d["trip"].update(stops=2, connections=["FRA", "LOS"], arrives_at="2027-01-23T06:00:00Z")
    digest_view.deal_card(d)
    assert any(t.endswith("(+2 days) · 14h 10m · 2 stops via Frankfurt, Lagos") for t in st.texts("write"))


def test_without_trip_details_never_says_nonstop(st):
    d = _card()
    d.update(trip=None, direct=True, nonstop=True, airlines="AC", unverified=True, trip_unverified=True)
    digest_view.deal_card(d)
    assert "Nonstop" not in st.all
    assert "Thu, Jan 21, 2027" in st.texts("write")
    cap = st.texts("caption")
    assert "Air Canada" in cap
    assert "⚠️ seats.aero didn't answer when we re-checked this one." in cap
    assert ("⚠️ Flights, stops and seats not confirmed — check on seats.aero before you transfer.") in cap


def test_quality_warnings(st):
    d = _card()
    d["trip"].update(mixed_cabin=True, lower_cabin_legs=["WAW–TLV economy"], airport_changes=["LHR→LGW"])
    d["slow"] = True
    digest_view.deal_card(d)
    warn = st.texts("warning")
    assert "Mixed cabin: WAW–TLV economy. Worth less than the value above." in warn
    assert "Airport change mid-trip: LHR→LGW. You'd have to get between airports yourself." in warn
    assert "🐢 Much longer than flying there directly." in st.texts("caption")


def test_clean_card_has_no_warnings(st):
    digest_view.deal_card(_card())
    assert st.texts("warning") == []
    assert "🐢" not in st.all and "not confirmed" not in st.all


def test_other_dates(st):
    d = _card()
    d["other_dates"] = [f"2027-02-{i:02d}" for i in range(1, 29)] + [f"2027-03-{i:02d}" for i in range(1, 4)]
    digest_view.deal_card(d)
    assert "**📅 Also available**" in st.texts("markdown")
    assert any(t.startswith("Feb 1, Feb 2,") and t.endswith(
        "Feb 12 and 19 more — on this many dates it's standard pricing, not a flash sale.")
        for t in st.texts("caption"))


def test_other_dates_in_another_year_show_the_year(st):
    d = _card()
    d["other_dates"] = ["2026-11-23", "2027-01-18"]
    digest_view.deal_card(d)
    assert "Nov 23, 2026, Jan 18" in st.texts("caption")


def test_return_option_and_note(st):
    d = _card()
    d["return_option"] = {"date": "2027-02-07", "points": 62400, "taxes_usd": 130.54,
                          "program": "Air Canada Aeroplan", "round_trip_points": 124800,
                          "note": "returns to JFK, not EWR"}
    digest_view.deal_card(d)
    cap = st.texts("caption")
    assert ("Sun, Feb 7, 2027 for 62,400 points + \\$131 — 124,800 points round trip on Air Canada Aeroplan") in cap
    assert "⚠️ returns to JFK, not EWR" in cap


def test_one_way_return_notes(st):
    d = _card()
    digest_view.deal_card(d)
    assert "↩️ One way only — no return award on these dates in this program." in st.texts("caption")
    st.calls.clear()
    d["returns_checked"] = False
    digest_view.deal_card(d)
    assert "↩️ One way. The way back wasn't checked on this run." in st.texts("caption")
    st.calls.clear()
    d.update(cabin="ECONOMY", points=20000)
    digest_view.deal_card(d)
    assert not any(t.startswith("↩️") for t in st.texts())


def test_alternatives_escaped(st):
    d = _card()
    d["alternatives"] = ["EWR via United, $5.60 taxes", "JFK via Flying Blue"]
    digest_view.deal_card(d)
    assert "EWR via United, \\$5.60 taxes · JFK via Flying Blue" in st.texts("caption")


def test_pay_plan_without_balances(st):
    digest_view.deal_card(_card())
    assert "**💳 How to pay**" in st.texts("markdown")
    assert len(st.texts("write")) >= 2  # flight line + pay summary


def test_links_and_award_age(st):
    d = _card()
    digest_view.deal_card(d)
    last = st.texts("caption")[-1]
    assert last.startswith("[Book via Air Canada Aeroplan →](https://example.com/book) · "
                           "[Check the cash fare on Google Flights →](https://www.google.com/travel/flights?q=")
    assert last.endswith(" · award seen today")
    for age, shown, want in [(1.2, 1, " · award seen 1 day ago, usually still there"),
                             (4.6, 4, " · award seen 4 days ago, usually still there"),
                             (6.7, 6, " · award seen 6 days ago, may be gone: check before transferring")]:
        st.calls.clear()
        d.update(age_days=age, age_shown=shown)
        digest_view.deal_card(d)
        assert st.texts("caption")[-1].endswith(want)
    st.calls.clear()
    d.update(age_days=None)
    digest_view.deal_card(d)
    last = st.texts("caption")[-1]
    assert "award seen" not in last and last.endswith("%20one%20way)")


def test_watchlist_uses_baseline_surplus_and_watch_bar(st):
    d = _card()
    d.update(watch_bar=3.0, watch_surplus_usd=33, surplus_usd=930.0, watch_label="Israel")
    digest_view.render_digest({"top": [], "scan": {}, "watchlist": [{"label": "Israel", "deals": [d]}]})
    assert "⭐ Watchlist: Israel" in st.texts("header")
    assert any(t.startswith("**\\$930 better**") and "above 3.00¢" in t for t in st.texts("markdown"))
    assert "\\$33 better" not in st.all
    # 3.86 >= 3.00 watch bar
    assert any(t.startswith("🟢 Book") for t in st.texts("markdown"))


def test_explicit_surplus_argument_wins(st):
    digest_view.deal_card(_card(), surplus=12.0)
    assert any(t.startswith("**\\$12 better**") for t in st.texts("markdown"))


def test_broken_card_costs_only_its_own_row(st):
    digest_view.deal_card({"origin": "JFK", "dest": "CPT", "cpp": "bad", "great_floor": 1.5})
    assert st.texts("warning") == ["Couldn't show JFK → CPT (TypeError)."]


def test_render_digest_without_digest(st, monkeypatch):
    monkeypatch.setattr(digest_view, "load_digest", lambda: None)
    digest_view.render_digest()
    assert st.texts("info") == ["No deal scan yet. Click **Scan now** above, or wait for the daily scan."]


def test_load_digest_missing_or_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(tmp_path / "missing.json"))
    assert digest_view.load_digest() is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(bad))
    assert digest_view.load_digest() is None
    good = tmp_path / "good.json"
    good.write_text('{"top": []}')
    monkeypatch.setattr(deal_finder, "DIGEST_PATH", str(good))
    assert digest_view.load_digest() == {"top": []}


def test_render_digest_sections(st):
    econ = _card()
    econ.update(cabin="ECONOMY", dest="MBJ")
    biz = _card()
    held = _card()
    held["dest"] = "LHR"
    digest = {"top": [econ, biz], "scan": {"candidates": 1234, "sources": ["aeroplan"]},
              "held_miles": [held],
              "watchlist": [{"label": "Kenya", "deals": [], "matched_awards": 0},
                            {"label": "Japan", "deals": [], "matched_awards": 1200, "priced": 7}],
              "estimate_drift": {"median_pct": 60, "p90_pct": 120, "n": 40}}
    digest_view.render_digest(digest)
    h = st.texts("header")
    assert h == ["✅ Book now with miles you already hold", "⭐ Watchlist: Kenya", "⭐ Watchlist: Japan",
                 "🏆 Top 2 of 1,234 awards"]
    cap = st.texts("caption")
    assert ("No award seats found for these dates — seats.aero may not cover this route, or the dates "
            "may fall outside the scan window.") in cap
    assert "Nothing clears this entry's bar yet (1,200 matching awards, 7 priced)." in cap
    assert any(t.startswith("⚠️ Fare estimates (used to choose what to price) were 60% off") and
               t.endswith("That's high: the deals picked for pricing may be poorly chosen.") for t in cap)
    assert st.texts("subheader") == ["Economy and premium economy", "🥂 Business and first"]
    md = st.texts("markdown")
    assert md.index("#### 1. Newark (EWR) → Montego Bay, Jamaica (MBJ)") < md.index(
        "#### 1. Newark (EWR) → Tel Aviv, Israel (TLV)")


def test_render_digest_outage_banner_and_good_drift(st):
    d = _card()
    d["rt_unavailable"] = True
    digest_view.render_digest({"top": [d], "scan": {}, "estimate_drift": {"median_pct": 20, "p90_pct": 50, "n": 9}})
    assert len(st.texts("warning")) == 1
    assert st.texts("warning")[0].startswith("Google Flights returned no round-trip fare for 1 of these 1 deals")
    assert any(t.startswith("✅ Fare estimates") and t.endswith("over 9 lookups.") for t in st.texts("caption"))


def test_time_helpers_tolerate_bad_input():
    assert digest_view._hm("not a time") == ""
    assert digest_view._hm(None) == ""
    assert digest_view._days_later("2027-01-21", "garbage") == ""
    assert digest_view._days_later(None, None) == ""
    assert digest_view._days_later("2027-01-21T22:00", "2027-01-21T23:00") == ""
    assert digest_view._duration(None) == "0m"
    assert digest_view._duration(125) == "2h 05m"


def test_held_miles_need_no_transfer(st, monkeypatch):
    monkeypatch.setattr(digest_view, "_balances", lambda: ({}, {"Air Canada Aeroplan": 70000}))
    digest_view.deal_card(_card())
    assert "No transfer needed." in st.texts("caption")
    assert "Transfers are irreversible" not in st.all
