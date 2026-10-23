"""Card pools, transfer ratios and which pool to burn first (cards_data.py)."""
from datetime import date

import pytest

import cards_data
from cards_data import Partner


def test_ratio_changes_on_its_announced_date():
    p = Partner("World of Hyatt", "hotel", "1:1", changes_on="2026-10-01", ratio_after="4:3")
    assert p.ratio_on(date(2026, 9, 30)) == "1:1"
    assert p.ratio_on(date(2026, 10, 1)) == "4:3"
    assert Partner("X", "airline", "2:1.5").ratio_on(date(2030, 1, 1)) == "2:1.5"


@pytest.mark.parametrize("ratio, mult", [("1:1", 1.0), ("2:1.5", 0.75), ("4:3", 0.75), ("1:2", 2.0)])
def test_transfer_ratio_multiplier(ratio, mult):
    assert cards_data.transfer_ratio_multiplier(ratio) == mult


def test_find_partner_pools_is_case_insensitive_and_blank_finds_nothing():
    keys = [pool.key for pool, _ in cards_data.find_partner_pools("  FLYING blue ")]
    assert keys == ["chase_ur", "cap1_miles", "citi_ty", "wf_rewards", "bilt"]
    assert cards_data.find_partner_pools("   ") == []
    assert cards_data.find_partner_pools("no such program") == []


def test_pools_without_a_transfer_card_are_inactive():
    assert cards_data.pool_is_active("chase_ur") is True  # Sapphire Preferred held
    assert cards_data.pool_is_active("bilt") is True  # transfers without the card
    assert cards_data.pool_is_active("cap1_miles") is False  # Venture X only planned
    assert cards_data.pool_is_active("cashback") is False  # never transferable


def test_rank_funding_skips_inactive_pools_and_prefers_a_covered_balance():
    matches = cards_data.find_partner_pools("Air France-KLM Flying Blue")
    ranked = cards_data.rank_funding_pools(matches, 60000, {"chase_ur": 10000, "bilt": 103000, "cap1_miles": 10**6})
    keys = [r["pool"].key for r in ranked]
    assert "cap1_miles" not in keys and "citi_ty" not in keys
    assert keys[0] == "bilt" and ranked[0]["covered"] is True and ranked[0]["pts_needed"] == 60000
    assert all(not r["covered"] for r in ranked[1:])


def test_rank_funding_converts_through_the_ratio_in_force(monkeypatch):
    pool = cards_data.Pool("p", "Pts", True, [Partner("Hotel", "hotel", "1:2")])
    monkeypatch.setitem(cards_data.POOLS, "p", pool)
    monkeypatch.setattr(cards_data, "pool_is_active", lambda k: k == "p")
    (r,) = cards_data.rank_funding_pools([(pool, pool.partners[0])], 30000, {"p": 15000})
    assert r["pts_needed"] == 15000 and r["covered"] is True and r["unique_partners"] == ["Hotel"]


def test_all_partner_names_are_sorted_and_unique():
    names = cards_data.all_partner_names()
    assert names == sorted(set(names)) and "United MileagePlus" in names
