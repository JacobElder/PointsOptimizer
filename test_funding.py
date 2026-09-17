import funding


def test_held_miles_cover_the_award():
    p = funding.plan("American Airlines AAdvantage", 9500, {}, {"American Airlines AAdvantage": 14440})
    assert p.covered_by_held and p.top_up == 0 and "already have" in p.summary


def test_transfer_from_the_best_active_pool_after_using_held_miles():
    p = funding.plan("United MileagePlus", 30000, {"chase_ur": 137000, "bilt": 103000},
                     {"United MileagePlus": 5980})
    assert p.top_up == 24020
    assert p.pools and {r["pool"].key for r in p.pools} == {"chase_ur", "bilt"}
    assert p.summary.startswith("Use your 5,980 United MileagePlus miles, then transfer 24,020")


def test_program_no_card_reaches():
    p = funding.plan("Delta SkyMiles", 20000, {"chase_ur": 137000}, {})
    assert p.pools == [] and "None of your points" in p.summary
