"""Test-wide safety: never scrape Google Flights, never touch the real quote store."""

import os

import pytest

import cash_quotes
import flight_search
import ledger
import seats_aero


@pytest.fixture(autouse=True)
def _isolate_cash_providers(monkeypatch, tmp_path):
    monkeypatch.setattr(flight_search, "FAST_FLIGHTS_DISABLED", True)
    # deal_finder.run() sets these globals; reset so they can't leak between tests
    monkeypatch.setattr(flight_search, "SERPAPI_MAX_CALLS", None)
    monkeypatch.setattr(flight_search, "serpapi_calls_made", 0)
    monkeypatch.setattr(ledger, "GIST_DISABLED", True)  # never touch the real balances Gist
    # Match CI, which has no secrets: ignore the local .streamlit/secrets.toml seats.aero key
    # so a test can't pass here only because the key exists. Tests needing it set the env var.
    monkeypatch.delenv("SEATS_AERO_API_KEY", raising=False)

    def _env_only_key():
        key = os.environ.get("SEATS_AERO_API_KEY")
        if not key:
            raise seats_aero.NotConfigured("not configured (tests)")
        return key

    monkeypatch.setattr(seats_aero, "_get_api_key", _env_only_key)
    monkeypatch.setattr(cash_quotes, "QUOTES_PATH", str(tmp_path / "cash_quotes.json"))
    flight_search.clear_cache()
