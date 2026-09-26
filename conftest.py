"""Test-wide safety: never scrape Google Flights, never touch the real quote store."""

import os

import pytest

import award_taxes
import cash_quotes
import flight_search
import ledger
import seats_aero
import valuation


@pytest.fixture(autouse=True)
def _isolate_cash_providers(monkeypatch, tmp_path):
    monkeypatch.setattr(flight_search, "FAST_FLIGHTS_DISABLED", True)
    # deal_finder.run() sets these globals; reset so they can't leak between tests.
    # 0, not None: None means "no cap", so the previous value left the real SerpApi
    # allowance reachable and uncapped from the test process.
    monkeypatch.setattr(flight_search, "SERPAPI_MAX_CALLS", 0)
    monkeypatch.setattr(flight_search, "serpapi_calls_made", 0)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
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
    # award_taxes.save() was writing to the REPO's program_taxes.json, which the daily
    # workflow commits: a test producing 5 observations for one program would have
    # pushed test fixtures into the published tax table.
    monkeypatch.setattr(award_taxes, "TAXES_PATH", str(tmp_path / "program_taxes.json"))

    # Module-level caches that no test restores, so order decides the outcome.
    monkeypatch.setattr(ledger, "_gist_seen_at", None)
    monkeypatch.setattr(ledger, "_gist_id", None)
    monkeypatch.setattr(ledger, "_gist_unavailable", False)
    monkeypatch.setattr(valuation, "_fx_cache", {})

    def _no_network(*a, **k):
        raise AssertionError("a test tried to fetch a live FX rate")

    monkeypatch.setattr(valuation.requests, "get", _no_network)
    flight_search.clear_cache()
