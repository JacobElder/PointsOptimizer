import deal_email


def test_is_configured_false_without_credentials(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(deal_email, "_get_credentials", deal_email._get_credentials)
    # Force the streamlit-secrets fallback to also miss by pointing at an empty secrets-like object
    import types
    fake_st = types.SimpleNamespace(secrets=types.SimpleNamespace(get=lambda k: None))
    import sys
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    assert deal_email.is_configured() is False


def test_digest_card_uses_place_and_airline_names():
    d = {"origin": "JFK", "dest": "GCM", "program": "American Airlines AAdvantage", "cabin": "ECONOMY",
         "date": "2026-12-28", "points": 10000, "taxes_usd": 5.6, "cash_price": 320.0,
         "cash_basis": "cheapest fare, any stops", "cash_is_approx": False, "one_way_cash": 320.0,
         "round_trip_half": None, "same_carrier_cash": 370.0, "cpp": 3.14, "great_floor": 1.5,
         "surplus_usd": 164, "direct": True, "airlines": "AA", "other_dates": ["2026-09-28"] * 8,
         "alternatives": ["JFK via JetBlue TrueBlue 19,300 pts (1.63¢ one-way)"], "held_miles": 14440,
         "bookable_now": True, "top_up_needed": 0, "new": True}
    html = deal_email._digest_card(d)
    assert "New York (JFK)" in html and "Grand Cayman, Cayman Islands (GCM)" in html
    assert "American Airlines" in html and "Mon, Dec 28, 2026" in html and "and 2 more" in html
    assert "Book with the 14,440 miles you have" in html and "google.com/travel/flights" in html


def test_send_digest_email_skips_empty_sections_and_sends_once(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(deal_email, "_get_credentials", lambda: ("me@example.com", "pw"))
    monkeypatch.setattr(deal_email.smtplib, "SMTP", FakeSMTP)
    d = {"origin": "EWR", "dest": "MBJ", "program": "JetBlue TrueBlue", "cabin": "ECONOMY", "date": "2026-10-06",
         "points": 4800, "taxes_usd": 49.6, "cash_price": 180.0, "cash_basis": "cheapest fare, any stops",
         "cpp": 2.72, "great_floor": 1.5, "surplus_usd": 58, "direct": True, "airlines": "B6"}
    deal_email.send_digest_email([("Book now", "sub", []), ("Top deals", "sub", [d])], "subj", "intro")
    assert len(sent) == 1
    html = sent[0].get_body(("html",)).get_content()
    assert "Top deals" in html and "Book now" not in html and "Montego Bay, Jamaica (MBJ)" in html


def test_card_says_what_it_could_not_confirm():
    """The email's job is to be honest about the unknowns: an award nobody
    re-checked must not read the same as one that was verified."""
    d = {"origin": "JFK", "dest": "VIE", "program": "United MileagePlus", "cabin": "BUSINESS",
         "date": "2026-10-04", "points": 88000, "taxes_usd": 0.0, "cash_price": 4752.0,
         "cash_basis": "cheapest fare — no round trip checked, so this may flatter the deal",
         "cpp": 5.4, "great_floor": 2.1, "baseline_cpp": 1.68, "surplus_usd": 3235,
         "direct": True, "airlines": "LX", "unverified": True, "taxes_unknown": True,
         "age_shown": 7, "age_days": 7.4,
         "rank_notes": ["we couldn't confirm the flights, stops or seats on this one"]}
    html = deal_email._digest_card(d)
    assert "Stops not confirmed" in html  # never guessed from seats.aero's `direct` flag
    assert "Flights and seats not confirmed" in html
    assert "taxes not reported" in html and "$0 taxes" not in html
    assert "Last confirmed 7 days ago" in html

    text = deal_email._digest_text(d)
    assert "stops not confirmed" in text and "Nonstop" not in text
    assert "taxes not reported" in text


def test_a_broken_card_costs_its_own_row_not_the_email():
    good = {"origin": "EWR", "dest": "MBJ", "program": "JetBlue TrueBlue", "cabin": "ECONOMY",
            "date": "2026-10-06", "points": 4800, "taxes_usd": 49.6, "cash_price": 180.0,
            "cash_basis": "cheapest fare, any stops", "cpp": 2.72, "great_floor": 1.5,
            "surplus_usd": 58, "direct": True, "airlines": "B6"}
    broken = {"origin": "JFK", "dest": "CPT", "cpp": None}  # missing everything else
    html, text = deal_email.build_digest([("Top deals", "sub", [broken, good])], "intro")
    assert "Montego Bay" in html and "Couldn't render" in html
    assert "couldn't render this deal" in text and "Montego Bay" in text


def test_watchlist_card_states_the_value_its_sentence_promises():
    """watch_surplus_usd measures against the watchlist bar; the sentence beside it
    compares with the program's baseline. Printing the first told the user a $930
    deal was worth $33."""
    d = {"origin": "JFK", "dest": "NBO", "program": "Air France-KLM Flying Blue", "cabin": "BUSINESS",
         "date": "2027-02-10", "points": 115000, "taxes_usd": 120.0, "cash_price": 2600.0,
         "cash_basis": "cheapest fare, any stops", "cpp": 2.15, "great_floor": 2.08,
         "baseline_cpp": 1.30, "surplus_usd": 930, "watch_surplus_usd": 33, "watch_bar": 2.08,
         "watch_label": "Kenya (migration season)", "direct": False, "airlines": "AF"}
    html = deal_email._digest_card(d)
    assert "$930 better" in html and "$33 better" not in html
    assert "above 2.08" in html  # the watchlist bar is still what it was judged against


# ── Coverage of every line the user reads on an email card ──────────────────
import copy
import json
import os
import sys
import types

import pytest

_REPO = os.path.dirname(os.path.abspath(__file__))


def _real_card() -> dict:
    """A card exactly as the digest stores it, varied per test."""
    with open(os.path.join(_REPO, "deal_digest.json")) as f:
        top = json.load(f)["top"]
    base = {"origin": "EWR", "dest": "TLV", "program": "Air Canada Aeroplan", "cabin": "BUSINESS",
            "date": "2027-01-21", "points": 62400, "taxes_usd": 79.81, "cash_price": 2488.5,
            "cash_basis": "half of a 7-night round trip", "cpp": 3.86, "great_floor": 2.625,
            "baseline_cpp": 2.1, "surplus_usd": 1098.0, "airlines": "AC, LO",
            "trip": {"flights": ["LO16 EWR–WAW", "H3155 WAW–TLV"], "connections": ["WAW"],
                     "duration_min": 850, "carriers": "THREE, LOT Polish", "seats": 1,
                     "mixed_cabin": False, "lower_cabin_legs": [], "airport_changes": [],
                     "stops": 1, "booking_url": "https://example.com/book?a=1&b=2",
                     "booking_label": "Book via Air Canada Aeroplan"}}
    # Prefer the committed digest's first card when it has the fields the tests vary.
    d = copy.deepcopy(top[0]) if top and {"points", "cash_price", "trip"} <= set(top[0]) else base
    for k in ("bookable_now", "held_miles", "top_up_needed", "watch_label", "watch_bar", "new",
              "slow", "unverified", "rt_unavailable", "taxes_unknown", "taxes_estimated",
              "history_pct", "rank_notes", "return_option", "other_dates", "same_carrier_cash",
              "cash_is_approx", "typical_fare", "cpp_at_typical", "age_shown", "age_days",
              "pay_summary", "alternatives"):
        d.pop(k, None)
    d.update({"origin": "EWR", "dest": "TLV", "program": "Air Canada Aeroplan", "cabin": "BUSINESS",
              "date": "2027-01-21", "points": 62400, "taxes_usd": 79.81, "cash_price": 2488.5,
              "cpp": 3.86, "great_floor": 2.625, "baseline_cpp": 2.1, "surplus_usd": 1098.0})
    d["trip"] = copy.deepcopy(base["trip"])
    return d


def _no_trip(d: dict) -> dict:
    d["trip"] = None
    return d


def test_held_miles_chip_says_how_much_to_transfer():
    d = _real_card()
    d.update(held_miles=14440, top_up_needed=47960, bookable_now=False)
    html = deal_email._digest_card(d)
    assert "You have 14,440 · transfer 47,960 more" in html
    assert "Book with the" not in html


def test_bookable_now_line_in_plain_text():
    d = _real_card()
    d.update(held_miles=70000, bookable_now=True)
    assert "Bookable now with the 70,000 miles you already hold" in deal_email._digest_text(d)


def test_watchlist_label_and_sent_before_chips_are_escaped():
    d = _real_card()
    d.update(watch_label="Israel <winter>", new=False)
    html = deal_email._digest_card(d)
    assert "⭐ Israel &lt;winter&gt;" in html and "<winter>" not in html
    assert "Sent before" in html


def test_new_or_unknown_card_is_not_marked_sent_before():
    d = _real_card()
    assert "Sent before" not in deal_email._digest_card(d)
    d["new"] = True
    assert "Sent before" not in deal_email._digest_card(d)


def test_quality_warning_chips():
    d = _real_card()
    d["trip"].update(mixed_cabin=True, lower_cabin_legs=["WAW–TLV economy"], airport_changes=["LHR→LGW"])
    d.update(slow=True, unverified=True, rt_unavailable=True, age_days=6.7)
    html = deal_email._digest_card(d)
    assert "⚠️ Mixed cabin: WAW–TLV economy" in html
    assert "🚕 Airport change: LHR→LGW" in html
    assert "🐢 Long itinerary" in html
    assert "Last confirmed 6 days ago — check before you transfer" in html
    assert "Flights and seats not confirmed" in html
    assert "Fare estimated: no round-trip price found" in html


def test_clean_card_has_no_warning_chips():
    html = deal_email._digest_card(_real_card())
    for s in ("Mixed cabin", "Airport change", "Long itinerary", "Last confirmed",
              "not confirmed", "Fare estimated", "Sent before"):
        assert s not in html


def test_fresh_award_has_no_staleness_chip():
    d = _real_card()
    d.update(age_shown=5, age_days=5.9)  # age_shown wins; 5 days is not stale
    assert "Last confirmed" not in deal_email._digest_card(d)


def test_stops_come_from_trip_details_only():
    d = _real_card()
    assert "Air Canada Aeroplan &middot; Business &middot; 1 stop" in deal_email._digest_card(d)
    assert "1 stop via Warsaw" in deal_email._digest_card(d)
    assert "Business · 1 stop" in deal_email._digest_text(d)
    d["trip"]["connections"] = ["WAW", "FRA"]
    assert "&middot; 2 stops" in deal_email._digest_card(d)
    assert "· 2 stops" in deal_email._digest_text(d)
    d["trip"]["connections"] = []
    assert "&middot; Nonstop" in deal_email._digest_card(d)
    assert "· Nonstop" in deal_email._digest_text(d)


def test_direct_flag_without_trip_never_says_nonstop():
    d = _no_trip(_real_card())
    d.update(direct=True, nonstop=True, airlines="AC")
    html = deal_email._digest_card(d)
    assert "Stops not confirmed" in html and "Nonstop" not in html
    assert "Air Canada" in html  # airlines row instead of flights row
    text = deal_email._digest_text(d)
    assert "stops not confirmed" in text and "Nonstop" not in text


def test_taxes_wording_estimated_and_reported():
    d = _real_card()
    html = deal_email._digest_card(d)
    assert "<b>62,400 points</b> + $80 taxes &amp; fees" in html
    assert "Award: 62,400 points + $80 taxes" in deal_email._digest_text(d)
    d["taxes_estimated"] = True
    html = deal_email._digest_card(d)
    assert "about $80 taxes &amp; fees" in html and "(estimated — seats.aero reported none)" in html
    text = deal_email._digest_text(d)
    assert "about $80 taxes (estimated)" in text and "about 3.86 cents/pt" in text


def test_taxes_unknown_headline_says_up_to():
    d = _real_card()
    d["taxes_unknown"] = True
    html = deal_email._digest_card(d)
    assert "up to 3.86&cent;" in html
    assert "taxes not reported — check them on the airline's site" in html


def test_usual_fare_line_only_when_today_is_higher():
    d = _real_card()
    d.update(typical_fare=2100, cpp_at_typical=3.24)
    html = deal_email._digest_card(d)
    assert "At this route's usual $2,100 fare: 3.24&cent;/pt" in html
    assert "at the usual $2,100: 3.24 cents/pt" in deal_email._digest_text(d)
    d.update(typical_fare=2657, cpp_at_typical=4.13)  # today's fare is below usual
    assert "route's usual" not in deal_email._digest_card(d)
    assert "at the usual" not in deal_email._digest_text(d)


def test_value_line_baseline_and_bar():
    d = _real_card()
    html = deal_email._digest_card(d)
    assert "$1,098 better</span> than spending these points the usual way (about 2.10&cent; each)" in html
    assert "We only flag Air Canada Aeroplan above 2.62&cent;/pt" in html
    d["baseline_cpp"] = None
    assert "than spending them the usual way" in deal_email._digest_card(d)


def test_watch_bar_overrides_program_floor():
    d = _real_card()
    d.update(watch_bar=3.0, watch_surplus_usd=150, watch_label="TLV")
    html = deal_email._digest_card(d)
    assert "above 3.00&cent;/pt" in html and "$1,098 better" in html and "$150 better" not in html


def test_same_carrier_fare_and_fare_notes():
    d = _real_card()
    d.update(same_carrier_cash=2000.0, same_carrier_code="LO", cash_is_approx=True, cabin="FIRST")
    html = deal_email._digest_card(d)
    # (2000 - 79.81) / 62400 * 100 = 3.077
    assert "on LOT Polish itself the fare is $2,000, which would make this 3.08&cent;/pt" in html
    assert "first class compared with the business fare" in html
    assert "fare borrowed from a nearby date" in html


def test_same_carrier_fare_without_code_and_too_close_to_matter():
    d = _real_card()
    d.update(same_carrier_cash=2000.0, same_carrier_code=None)
    assert "on the airline flying it itself the fare is $2,000" in deal_email._digest_card(d)
    d["same_carrier_cash"] = 2470.0  # within 2% of the fare: not worth a line
    assert "itself the fare" not in deal_email._digest_card(d)


def test_price_history_phrases():
    d = _real_card()
    d.update(history_pct=1.0, history_days=28)
    assert "Price history: the cheapest this route has been in the last 28 days" in deal_email._digest_card(d)
    d["history_pct"] = 0.91
    assert "Price history: cheaper than 91% of the last 28 days on this route" in deal_email._digest_card(d)
    d["history_pct"] = 0.2
    assert "Price history: pricier than usual for this route (80% of days were cheaper)" in deal_email._digest_card(d)
    d["history_days"] = 9  # too little history to say anything
    assert "Price history" not in deal_email._digest_card(d)


def test_rank_notes_are_escaped():
    d = _real_card()
    d["rank_notes"] = ["mixed cabin <1 leg>", "slow"]
    assert "Ranked lower because mixed cabin &lt;1 leg&gt;; slow" in deal_email._digest_card(d)


def test_return_option_and_its_note():
    d = _real_card()
    d["return_option"] = {"date": "2027-02-07", "points": 62400, "taxes_usd": 130.54,
                          "program": "Air Canada Aeroplan", "round_trip_points": 124800,
                          "note": "return is from JFK & not EWR"}
    html = deal_email._digest_card(d)
    assert "Sun, Feb 7, 2027 for <b>62,400 points</b> + $131" in html
    assert "124,800 points round trip on Air Canada Aeroplan" in html
    assert "⚠️ return is from JFK &amp; not EWR" in html


def test_one_way_return_rows():
    d = _real_card()
    d["returns_checked"] = True
    assert "No return award on these dates in this program — this is a one way" in deal_email._digest_card(d)
    d["returns_checked"] = False
    assert "One way. We didn't check the way back on this run." in deal_email._digest_card(d)
    d.update(cabin="ECONOMY", points=20000)  # cheap economy: no return row at all
    html = deal_email._digest_card(d)
    assert "One way" not in html and "No return award" not in html


def test_other_dates_show_year_when_it_differs():
    d = _real_card()
    d["other_dates"] = ["2026-11-23", "2027-01-18"]
    html = deal_email._digest_card(d)
    assert "Also: Nov 23, 2026, Jan 18</span>" in html
    assert "(+2 more)" in deal_email._digest_text(d)


def test_many_other_dates_say_standard_pricing():
    d = _real_card()
    d["other_dates"] = [f"2027-02-{i:02d}" for i in range(1, 29)] + [f"2027-03-{i:02d}" for i in range(1, 4)]
    html = deal_email._digest_card(d)
    assert "and 25 more" in html
    assert "standard pricing, not a flash sale. This date is down to its last seats, though." in html
    d["trip"]["seats"] = 9
    assert "standard pricing, not a flash sale. No rush." in deal_email._digest_card(d)


def test_seats_flights_pay_and_alternatives_rows():
    d = _real_card()
    d.update(pay_summary="Transfer 63,000 Chase → Aeroplan", alternatives=["EWR via United <88k>"])
    html = deal_email._digest_card(d)
    assert "1 left when we checked" in html
    assert "LO16 EWR–WAW · H3155 WAW–TLV" in html and "14h 10m" in html
    assert "Transfer 63,000 Chase → Aeroplan" in html
    assert "EWR via United &lt;88k&gt;" in html


def test_booking_link_is_escaped():
    d = _real_card()
    d["trip"]["booking_label"] = "Book <now>"
    html = deal_email._digest_card(d)
    assert 'href="https://example.com/book?a=1&amp;b=2"' in html
    assert "Book &lt;now&gt; &rarr;" in html


def test_program_and_basis_are_html_escaped():
    d = _real_card()
    d.update(program="A&B <Miles>", cash_basis="cheapest <fare>")
    html = deal_email._digest_card(d)
    assert "A&amp;B &lt;Miles&gt; &middot; Business" in html
    assert "cheapest &lt;fare&gt;" in html and "<Miles>" not in html


def test_plain_text_carries_the_essential_facts():
    d = _real_card()
    d.update(cash_basis="half of a 7-night round trip")
    text = deal_email._digest_text(d)
    assert text.splitlines() == [
        "Newark (EWR) -> Tel Aviv, Israel (TLV)  |  3.86 cents/pt",
        "  Air Canada Aeroplan · Business · 1 stop",
        "  Award: 62,400 points + $80 taxes",
        "  Cash fare: $2,488 (half of a 7-night round trip)",
        "  Date: Thu, Jan 21, 2027",
    ]


def test_build_digest_puts_warning_first():
    d = _real_card()
    html, text = deal_email.build_digest([("Top deals", "Ranked", [d])], "3 new deals",
                                         warning="Google coverage was partial <today>")
    assert "⚠️ Google coverage was partial &lt;today&gt;" in html
    assert html.index("partial") < html.index("3 new deals") < html.index("Top deals")
    assert text.splitlines()[:4] == ["WARNING: Google coverage was partial <today>", "", "3 new deals", ""]
    assert "TOP DEALS" in text


def test_build_digest_without_warning_has_no_banner():
    html, text = deal_email.build_digest([("Top", "s", [_real_card()]), ("Empty", "s", [])], "intro")
    assert "WARNING" not in text and "⚠️" not in html.split("Top")[0]
    assert "Empty" not in html and "EMPTY" not in text


def test_send_digest_email_sends_nothing_when_all_sections_empty(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(deal_email, "_get_credentials", boom)
    monkeypatch.setattr(deal_email.smtplib, "SMTP", boom)
    assert deal_email.send_digest_email([("A", "s", []), ("B", "s", [])], "subj", "intro") is None


def test_send_digest_email_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setitem(sys.modules, "streamlit",
                        types.SimpleNamespace(secrets=types.SimpleNamespace(get=lambda k: None)))

    def boom(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(deal_email.smtplib, "SMTP", boom)
    with pytest.raises(deal_email.NotConfigured, match="GMAIL_APP_PASSWORD"):
        deal_email.send_digest_email([("Top", "s", [_real_card()])], "subj", "intro")


def test_send_digest_email_message_headers_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, pw):
            calls.append(("login", user, pw))

        def send_message(self, msg):
            calls.append(("send", msg))

    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pw")
    monkeypatch.setattr(deal_email.smtplib, "SMTP", FakeSMTP)
    deal_email.send_digest_email([("Top", "s", [_real_card()])], "3 deals", "intro", warning="partial")
    assert calls[:3] == [("connect", "smtp.gmail.com", 587), ("starttls",), ("login", "me@example.com", "app-pw")]
    msg = calls[3][1]
    assert msg["Subject"] == "3 deals" and msg["From"] == "me@example.com" and msg["To"] == "me@example.com"
    assert msg.get_body(("plain",)).get_content().startswith("WARNING: partial")


def test_credentials_fall_back_to_streamlit_secrets(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "env-pw")
    secrets = {"GMAIL_ADDRESS": "s@example.com", "GMAIL_APP_PASSWORD": "secret-pw"}
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets=types.SimpleNamespace(get=secrets.get)))
    assert deal_email._get_credentials() == ("s@example.com", "env-pw")  # env wins where set
    assert deal_email.is_configured() is True


def test_credentials_survive_a_broken_streamlit(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    class Boom:
        def get(self, k):
            raise RuntimeError("no secrets.toml")

    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets=Boom()))
    with pytest.raises(deal_email.NotConfigured):
        deal_email._get_credentials()


def test_safe_text_fallback_names_the_route():
    assert deal_email._safe_text({"origin": "JFK", "dest": "CPT"}) == "  JFK -> CPT: couldn't render this deal"
    assert "Couldn't render JFK &rarr; &lt;x&gt;" in deal_email._row_fallback({"origin": "JFK", "dest": "<x>"})
