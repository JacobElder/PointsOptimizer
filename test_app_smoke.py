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
    at.run(timeout=30)
    assert not at.exception, [str(e) for e in at.exception]


def test_navigation_lists_every_view():
    with open("app.py") as f:
        router = f.read()
    for view in glob.glob("views/*.py"):
        assert view in router, f"{view} is not in app.py navigation"


@pytest.mark.parametrize("mode", ["One date", "Date range", "Any time", "Outbound + return"])
def test_flight_search_modes_render(mode):
    at = AppTest.from_file("views/flight_search.py")
    at.run(timeout=30)
    at.radio(key="fs_mode").set_value(mode).run(timeout=30)
    assert not at.exception, [str(e) for e in at.exception]
