"""Loads app.py and every page with Streamlit's AppTest to catch import/runtime
errors (widget API changes, etc.) that a plain HTTP-200 check would miss --
this doesn't click anything, just verifies a fresh page load doesn't raise."""

import glob

import pytest
from streamlit.testing.v1 import AppTest

SCRIPTS = ["app.py"] + sorted(glob.glob("pages/*.py"))


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_loads_without_exception(script):
    at = AppTest.from_file(script)
    at.run(timeout=30)
    assert not at.exception, [str(e) for e in at.exception]
