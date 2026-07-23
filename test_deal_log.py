import json

import deal_log


def test_load_defaults_missing_keys(tmp_path, monkeypatch):
    path = tmp_path / "deal_log.json"
    path.write_text(json.dumps({"deals": []}))  # simulates an older file with no "pending" key
    monkeypatch.setattr(deal_log, "DEAL_LOG_PATH", str(path))

    data = deal_log.load()

    assert data == {"processed_message_ids": [], "deals": [], "pending": []}


def test_load_missing_file_returns_empty_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(deal_log, "DEAL_LOG_PATH", str(tmp_path / "does_not_exist.json"))

    data = deal_log.load()

    assert data == {"processed_message_ids": [], "deals": [], "pending": []}


def test_make_key_is_case_and_whitespace_insensitive():
    a = deal_log.make_key("Flying Blue", "jfk", "mad", "business", "2026-11-04", 43000)
    b = deal_log.make_key(" flying blue ", "JFK", "MAD", "BUSINESS", "2026-11-04", 43000)
    assert a == b


def test_existing_keys_covers_both_deals_and_pending():
    data = {
        "deals": [dict(program="United MileagePlus", origin="SFO", dest="NRT",
                       cabin="FIRST", date="2027-01-15", points=80000)],
        "pending": [dict(program="JetBlue TrueBlue", origin="EWR", dest="MBJ",
                         cabin="ECONOMY", date="2026-10-28", points=14200)],
    }
    keys = deal_log.existing_keys(data)
    assert deal_log.make_key("United MileagePlus", "SFO", "NRT", "FIRST", "2027-01-15", 80000) in keys
    assert deal_log.make_key("JetBlue TrueBlue", "EWR", "MBJ", "ECONOMY", "2026-10-28", 14200) in keys
    assert len(keys) == 2


def test_is_great_uses_lower_bar_for_economy():
    assert deal_log.is_great(dict(cabin="ECONOMY", cpp=1.6)) is True
    assert deal_log.is_great(dict(cabin="ECONOMY", cpp=1.4)) is False
    assert deal_log.is_great(dict(cabin="PREMIUM_ECONOMY", cpp=1.6)) is True


def test_is_great_uses_higher_bar_for_business_and_first():
    assert deal_log.is_great(dict(cabin="BUSINESS", cpp=2.1)) is True
    assert deal_log.is_great(dict(cabin="BUSINESS", cpp=1.6)) is False
    assert deal_log.is_great(dict(cabin="FIRST", cpp=1.9)) is False


def test_is_great_false_when_cpp_missing():
    assert deal_log.is_great(dict(cabin="ECONOMY", cpp=None)) is False
    assert deal_log.is_great(dict(cabin="ECONOMY")) is False


# FIX 7 -- verdict_for and is_great must never contradict: BOOK <=> is_great.
def test_verdict_book_agrees_with_is_great_at_the_cabin_bar():
    # Economy at 1.6c: great bar is 1.5 -> both say standout/BOOK (previously the
    # flat 1.7 book floor made this "BORDERLINE" while it was still emailed).
    assert deal_log.verdict_for(1.6, "ECONOMY") == "BOOK"
    assert deal_log.is_great(dict(cabin="ECONOMY", cpp=1.6)) is True
    # Business at 1.8c: great bar is 2.0 -> neither standout nor BOOK.
    assert deal_log.verdict_for(1.8, "BUSINESS") == "BORDERLINE"
    assert deal_log.is_great(dict(cabin="BUSINESS", cpp=1.8)) is False


def test_verdict_for_skip_and_no_cash_price():
    assert deal_log.verdict_for(0.9, "ECONOMY") == "SKIP"
    assert deal_log.verdict_for(None, "BUSINESS") == "NO CASH PRICE"


def test_is_valid_pending_accepts_a_well_formed_entry():
    entry = dict(origin="JFK", dest="CTG", program="Aeroplan", cabin="BUSINESS",
                 date="2026-08-31", points=40000, taxes=102.7, currency="CAD")
    assert deal_log.is_valid_pending(entry) is True


def test_is_valid_pending_rejects_malformed_entries():
    base = dict(origin="JFK", dest="CTG", program="Aeroplan", cabin="BUSINESS",
                date="2026-08-31", points=40000, taxes=102.7, currency="CAD")
    assert deal_log.is_valid_pending({**base, "points": 0}) is False        # zero points
    assert deal_log.is_valid_pending({**base, "points": None}) is False      # missing points
    assert deal_log.is_valid_pending({**base, "date": "Aug 31"}) is False    # bad date format
    assert deal_log.is_valid_pending({**base, "taxes": "n/a"}) is False       # non-numeric taxes
    assert deal_log.is_valid_pending({k: v for k, v in base.items() if k != "origin"}) is False
    assert deal_log.is_valid_pending("not a dict") is False


def test_existing_keys_tolerates_a_malformed_entry():
    data = {
        "deals": [dict(program="United", origin="SFO", dest="NRT", cabin="FIRST",
                       date="2027-01-15", points=80000)],
        "pending": [dict(program="Aeroplan", origin="JFK", dest="CTG")],  # missing fields
    }
    keys = deal_log.existing_keys(data)  # must not raise
    assert len(keys) == 1
