import funding


def test_held_miles_cover_the_award():
    p = funding.plan("American Airlines AAdvantage", 9500, {}, {"American Airlines AAdvantage": 14440})
    assert p.covered_by_held and p.top_up == 0
    assert p.summary == "Pay with 9,500 of the 14,440 American Airlines AAdvantage miles you already have"


def test_transfer_from_the_best_active_pool_after_using_held_miles():
    p = funding.plan("United MileagePlus", 30000, {"chase_ur": 137000, "bilt": 103000},
                     {"United MileagePlus": 5980})
    assert p.top_up == 24020
    assert p.pools and {r["pool"].key for r in p.pools} == {"chase_ur", "bilt"}
    # Issuers transfer in 1,000-point blocks, so 24,020 is rounded up to 25,000.
    assert p.summary.startswith("Use your 5,980 United MileagePlus miles, then transfer 25,000")


def test_program_no_card_reaches():
    p = funding.plan("Delta SkyMiles", 20000, {"chase_ur": 137000}, {})
    assert p.pools == [] and "None of your points" in p.summary


def test_active_transfer_bonus_reduces_points_needed(tmp_path, monkeypatch):
    f = tmp_path / "bonuses.json"
    f.write_text('{"bonuses": [{"pool": "chase_ur", "partner": "Air Canada Aeroplan", "bonus_pct": 20, "ends": "2099-01-01"},'
                 ' {"pool": "bilt", "partner": "Air Canada Aeroplan", "bonus_pct": 50, "ends": "2000-01-01"}]}')
    monkeypatch.setattr(funding, "BONUSES_PATH", str(f))
    p = funding.plan("Air Canada Aeroplan", 60000, {"chase_ur": 137000, "bilt": 103000}, {})
    chase = next(r for r in p.pools if r["pool"].key == "chase_ur")
    bilt = next(r for r in p.pools if r["pool"].key == "bilt")
    assert round(chase["pts_needed"]) == 50000 and "bonus" in chase
    assert bilt["pts_needed"] == 60000 and "bonus" not in bilt  # expired bonus ignored
    assert "+20% transfer bonus" in p.summary


def test_a_transfer_bonus_beats_the_flexible_pool_tiebreak(monkeypatch):
    """A bonus can save 20,000 points on one award; pool "flexibility" is a
    tiebreak. Sorting flexibility first would spend Chase at 1:1 while a Bilt
    Rent Day bonus sat unused."""
    monkeypatch.setattr(funding, "active_bonus",
                        lambda pool_key, program: ({"bonus_pct": 25, "ends": "2026-10-31"}
                                                   if pool_key == "bilt" else None))
    p = funding.plan("Air Canada Aeroplan", 100000, {"chase_ur": 200000, "bilt": 200000}, {})
    assert p.pools[0]["pool"].key == "bilt"
    assert "+25% transfer bonus" in p.summary


def test_covered_accounts_for_the_1000_point_transfer_increment():
    """Balance clears pts_needed but not the rounded-up transfer the summary
    actually instructs, which read as "covered" next to "not enough"."""
    p = funding.plan("United MileagePlus", 58900, {"chase_ur": 58900}, {})  # 1:1, no bonus
    top = p.pools[0]
    assert top["covered"] is False and "not enough" in p.summary
