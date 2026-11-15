"""Loads the app and every page with Streamlit's AppTest to catch import/runtime
errors (widget API changes, etc.) that a plain HTTP-200 check would miss --
this doesn't click anything, just verifies a fresh page load doesn't raise."""

import glob

import pytest
from streamlit.testing.v1 import AppTest

SCRIPTS = ["app.py"] + sorted(glob.glob("views/*.py"))


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_loads_without_exception(script):
    at = AppTest.from_file(script)
    at.run(timeout=90)
    # deal_card swallows a rendering failure into a warning, so `not at.exception`
    # can't see a broken card: every card on the page could degrade and CI stay green.
    assert not [w for w in at.warning if "Couldn't show" in str(w.value)]
    assert not at.exception, [str(e) for e in at.exception]


def test_navigation_lists_every_view():
    with open("app.py") as f:
        router = f.read()
    for view in glob.glob("views/*.py"):
        assert view in router, f"{view} is not in app.py navigation"


def _page_text(at) -> str:
    return "\n".join(str(e.value) for kind in ("title", "markdown", "caption", "info", "success", "warning")
                     for e in getattr(at, kind))


def test_wallet_shows_the_ratio_in_force_today():
    """Chase -> Hyatt went 1:1 -> 4:3 on 2026-10-01; the page printed the old ratio."""
    import cards_data
    hyatt = next(p for p in cards_data.POOLS["chase_ur"].partners if p.name == "World of Hyatt")
    at = AppTest.from_file("views/wallet.py")
    at.run(timeout=90)
    text = _page_text(at)
    assert at.title[0].value == "💳 Wallet"  # matches the nav entry
    assert f"- World of Hyatt ({hyatt.ratio_on()})" in text  # Bilt's Hyatt stays 1:1, so no "not in"


def test_roadmap_hyatt_note_is_not_in_the_future_tense_after_the_change():
    import datetime
    at = AppTest.from_file("views/roadmap.py")
    at.run(timeout=90)
    text = _page_text(at)
    if datetime.date.today() >= datetime.date(2026, 10, 1) and "Hyatt" in text:
        assert "drops to 4:3 on Oct 1, 2026" not in text
        assert "dropped to 4:3 on Oct 1, 2026" in text


@pytest.mark.parametrize("mode",["One date", "Date range", "Any time", "Outbound + return"])
def test_flight_search_modes_render(mode, monkeypatch):
    # The page stops early without a seats.aero key; CI has none, so provide a dummy.
    monkeypatch.setenv("SEATS_AERO_API_KEY", "test-key")
    at = AppTest.from_file("views/flight_search.py")
    at.run(timeout=90)
    at.radio(key="fs_mode").set_value(mode).run(timeout=90)
    assert not at.exception, [str(e) for e in at.exception]
