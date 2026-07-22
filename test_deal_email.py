import deal_email


def test_format_deal_includes_all_key_fields():
    deal = dict(
        origin="EWR", dest="PTY", program="Air Canada Aeroplan", cabin="ECONOMY",
        date="2027-02-05", flight_number="UA4435, CM444", points=12500,
        taxes=89.50, currency="CAD", cash_price=458.0, cpp=3.14,
    )
    text = deal_email._format_deal(deal)
    assert "EWR -> PTY" in text
    assert "Air Canada Aeroplan" in text
    assert "Economy" in text
    assert "UA4435, CM444" in text
    assert "2027-02-05" in text
    assert "12,500" in text
    assert "89.50 CAD" in text
    assert "$458.00" in text
    assert "3.14 cents per point" in text


def test_format_deal_handles_missing_flight_number():
    deal = dict(
        origin="JFK", dest="MAD", program="Air France-KLM Flying Blue", cabin="BUSINESS",
        date="2026-11-04", points=43000, taxes=33.50, currency="USD",
        cash_price=1909.0, cpp=4.36,
    )
    text = deal_email._format_deal(deal)
    assert "unknown flight" in text


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
