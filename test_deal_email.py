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
