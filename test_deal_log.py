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
