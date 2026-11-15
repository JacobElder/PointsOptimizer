"""Tests for seats_aero: the API key lookup (the searches are tested in
test_award_scanner.py and test_award_trips.py)."""

import pytest

import seats_aero


def test_no_key_means_not_configured():
    assert seats_aero.is_configured() is False  # conftest: env-only key, unset


def test_key_from_env_means_configured(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "k")
    assert seats_aero.is_configured() is True


def test_real_key_lookup_reads_env_then_streamlit_secrets(monkeypatch):
    """conftest replaces _get_api_key; load a private copy of the module to test the real one
    without disturbing the shared module (other modules hold its exception classes)."""
    import importlib.util
    import sys
    import types

    spec = importlib.util.spec_from_file_location("_seats_aero_copy", seats_aero.__file__)
    copy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_seats_aero_copy", copy)  # dataclasses look the module up
    spec.loader.exec_module(copy)
    monkeypatch.setenv("SEATS_AERO_API_KEY", "from-env")
    assert copy._get_api_key() == "from-env"
    monkeypatch.delenv("SEATS_AERO_API_KEY")
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={"SEATS_AERO_API_KEY": "s"}))
    assert copy._get_api_key() == "s"
    monkeypatch.setitem(sys.modules, "streamlit", types.SimpleNamespace(secrets={}))
    with pytest.raises(copy.NotConfigured):
        copy._get_api_key()
