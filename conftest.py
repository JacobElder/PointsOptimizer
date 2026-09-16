"""Test-wide safety: never scrape Google Flights, never touch the real quote store."""

import pytest

import cash_quotes
import flight_search


@pytest.fixture(autouse=True)
def _isolate_cash_providers(monkeypatch, tmp_path):
    monkeypatch.setattr(flight_search, "FAST_FLIGHTS_DISABLED", True)
    monkeypatch.setattr(cash_quotes, "QUOTES_PATH", str(tmp_path / "cash_quotes.json"))
    flight_search.clear_cache()
