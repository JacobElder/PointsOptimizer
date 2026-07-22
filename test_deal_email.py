import deal_email

ECONOMY_DEAL = dict(
    origin="EWR", dest="PTY", program="Air Canada Aeroplan", cabin="ECONOMY",
    date="2027-02-05", flight_number="UA4435, CM444", points=12500,
    taxes=89.50, currency="CAD", cash_price=458.0, cpp=3.14,
)

BUSINESS_DEAL = dict(
    origin="JFK", dest="MAD", program="Air France-KLM Flying Blue", cabin="BUSINESS",
    date="2026-11-04", points=43000, taxes=33.50, currency="USD",
    cash_price=1909.0, cpp=4.36,
)


def test_format_deal_includes_all_key_fields():
    text = deal_email._format_deal(ECONOMY_DEAL)
    assert "EWR -> PTY" in text
    assert "Air Canada Aeroplan" in text
    assert "Economy" in text
    assert "UA4435, CM444" in text
    assert "2027-02-05" in text
    assert "12,500" in text
    assert "89.50 CAD" in text
    assert "$458.00" in text
    assert "3.14 cents/pt" in text


def test_format_deal_handles_missing_flight_number():
    deal = {**BUSINESS_DEAL, "flight_number": None}
    text = deal_email._format_deal(deal)
    assert "unknown flight" in text


def test_deal_card_html_escapes_and_includes_cpp():
    card = deal_email._deal_card_html(ECONOMY_DEAL)
    assert "EWR" in card and "PTY" in card
    assert "3.14" in card
    assert "12,500" in card
    assert "$458.00" in card


def test_deal_card_html_escapes_html_special_chars():
    deal = {**ECONOMY_DEAL, "program": "Air <script>alert('x')</script>"}
    card = deal_email._deal_card_html(deal)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card


def test_build_html_splits_economy_and_premium_sections():
    html = deal_email._build_html([ECONOMY_DEAL, BUSINESS_DEAL], best=BUSINESS_DEAL)
    econ_idx = html.index("Economy / Premium Economy")
    biz_idx = html.index("Business / First")
    # unique to each deal's card (not the summary line), to place them unambiguously
    econ_card_idx = html.index("$458.00")
    biz_card_idx = html.index("$1,909.00")
    assert econ_idx < econ_card_idx < biz_idx
    assert biz_idx < biz_card_idx


def test_build_html_omits_empty_section():
    html = deal_email._build_html([ECONOMY_DEAL], best=ECONOMY_DEAL)
    assert "Economy / Premium Economy" in html
    assert "Business / First" not in html


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
