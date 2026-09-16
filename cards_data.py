"""
Static reference data: your card portfolio, the point/mile pools they feed,
and each pool's transfer partners.

Transfer ratios and partner rosters change without notice — treat these as a
starting point and confirm current terms on the issuer's site before booking.

Last verified 2026-09-16: Chase and Citi against at least two current sources,
Capital One against capitalone.com, Bilt against point.me + awardtravelfinder
(only partners both list), Wells Fargo 2026-07. Still unconfirmed: whether
Capital One transfers to Virgin Atlantic directly (only Virgin Red is on
capitalone.com's list).
"""

from dataclasses import dataclass, field


@dataclass
class Card:
    name: str
    issuer: str
    pool_key: str  # which POOL this card's spend accrues to ("" if none)
    status: str  # "held", "planned", or "closed" (account closed; points may remain)
    unlocks_transfer: bool = False  # True if holding this card unlocks transfer-out for its pool
    notes: str = ""


@dataclass
class Partner:
    name: str
    kind: str  # "airline" or "hotel"
    ratio: str  # e.g. "1:1" or "2:1.5"


@dataclass
class Pool:
    key: str
    currency_name: str
    transferable: bool  # whether this currency can ever be transferred to partners
    partners: list = field(default_factory=list)
    portal_rate_cents: float | None = None  # fixed cent value if redeemed via issuer travel portal
    fixed_value_note: str = ""
    # True when the loyalty membership itself allows transfers, with no card required.
    transfers_without_card: bool = False


# ── Pools ──────────────────────────────────────────────────────────────────
POOLS: dict[str, Pool] = {
    "chase_ur": Pool(
        key="chase_ur",
        currency_name="Chase Ultimate Rewards",
        transferable=True,
        # Chase "Points Boost" (Oct 2025): fixed 1.25¢ (CSP) / 1.5¢ (CSR) portal value
        # applies only to points earned before Oct 26, 2025, and only until Oct 26, 2027.
        # New points redeem at 1.0¢ plus selective boosts, so 1.0 is the honest floor.
        portal_rate_cents=1.0,
        partners=[
            Partner("Aer Lingus AerClub", "airline", "1:1"),
            Partner("Air Canada Aeroplan", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("British Airways Executive Club", "airline", "1:1"),
            Partner("Iberia Plus", "airline", "1:1"),
            Partner("JetBlue TrueBlue", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
            Partner("Southwest Rapid Rewards", "airline", "1:1"),
            Partner("United MileagePlus", "airline", "1:1"),
            Partner("Virgin Atlantic Flying Club", "airline", "1:1"),
            Partner("IHG One Rewards", "hotel", "1:1"),
            Partner("Marriott Bonvoy", "hotel", "1:1"),
            # 4:3 for Sapphire Preferred holders from Oct 1, 2026 (immediately for
            # CSP applications on/after Jun 15, 2026). Still 1:1 with Sapphire Reserve.
            Partner("World of Hyatt", "hotel", "4:3"),
            Partner("Wyndham Rewards", "hotel", "1:1"),  # added Feb 2026
        ],
    ),
    "cap1_miles": Pool(
        key="cap1_miles",
        currency_name="Capital One Miles",
        transferable=True,
        portal_rate_cents=1.0,  # up to 2.0 via Venture X portal in some cases
        partners=[
            Partner("Air Canada Aeroplan", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("Accor Live Limitless", "hotel", "2:1"),
            Partner("Aeromexico Rewards", "airline", "1:1"),
            Partner("Avianca LifeMiles", "airline", "1:1"),
            Partner("British Airways Executive Club", "airline", "1:1"),
            Partner("Cathay Pacific Asia Miles", "airline", "1:1"),
            Partner("Choice Privileges", "hotel", "1:1"),
            Partner("Emirates Skywards", "airline", "2:1.5"),  # devalued Jan 2026
            Partner("Etihad Guest", "airline", "1:1"),
            Partner("EVA Air Infinity MileageLands", "airline", "2:1.5"),
            Partner("Finnair Plus", "airline", "1:1"),
            Partner("JAL Mileage Bank", "airline", "2:1.5"),
            Partner("JetBlue TrueBlue", "airline", "5:3"),
            Partner("Qantas Frequent Flyer", "airline", "1:1"),
            Partner("Qatar Airways Privilege Club", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
            Partner("TAP Air Portugal Miles&Go", "airline", "1:1"),
            Partner("Turkish Airlines Miles&Smiles", "airline", "1:1"),
            Partner("Virgin Red", "airline", "1:1"),
            Partner("Wyndham Rewards", "hotel", "1:1"),
        ],
    ),
    "citi_ty": Pool(
        key="citi_ty",
        currency_name="Citi ThankYou Points",
        transferable=True,
        portal_rate_cents=1.0,
        partners=[
            Partner("Aer Lingus AerClub", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("American Airlines AAdvantage", "airline", "1:1"),  # Strata Premier/Elite only
            Partner("Emirates Skywards", "airline", "1:0.8"),
            Partner("Etihad Guest", "airline", "1:1"),
            Partner("Avianca LifeMiles", "airline", "1:1"),
            Partner("Cathay Pacific Asia Miles", "airline", "1:1"),
            Partner("Choice Privileges", "hotel", "1:1.5"),  # cut from 1:2 on Apr 19, 2026
            Partner("EVA Air Infinity MileageLands", "airline", "1:1"),
            Partner("JetBlue TrueBlue", "airline", "1:1"),
            Partner("Malaysia Airlines Enrich", "airline", "1:1"),
            Partner("Qantas Frequent Flyer", "airline", "1:1"),
            Partner("Qatar Airways Privilege Club", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
            Partner("Thai Airways Royal Orchid Plus", "airline", "1:1"),
            Partner("Turkish Airlines Miles&Smiles", "airline", "1:1"),
            Partner("Virgin Atlantic Flying Club", "airline", "1:1"),
            Partner("Wyndham Rewards", "hotel", "1:1"),
        ],
    ),
    "wf_rewards": Pool(
        key="wf_rewards",
        currency_name="Wells Fargo Rewards",
        transferable=True,
        portal_rate_cents=1.0,
        partners=[
            Partner("Aer Lingus AerClub", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("Avianca LifeMiles", "airline", "1:1"),
            Partner("British Airways Executive Club", "airline", "1:1"),
            Partner("Cathay Pacific Asia Miles", "airline", "1:1"),
            Partner("Iberia Plus", "airline", "1:1"),
            Partner("JetBlue TrueBlue", "airline", "1:1"),
            Partner("Virgin Atlantic Flying Club", "airline", "1:1"),
            Partner("Choice Privileges", "hotel", "1:2"),
            Partner("Wyndham Rewards", "hotel", "1:2"),
        ],
    ),
    "bilt": Pool(
        key="bilt",
        currency_name="Bilt Points",
        transferable=True,
        portal_rate_cents=1.0,
        # Bilt Mastercard is closed but the points were kept; user confirmed 2026-09-16
        # that transfers still work without the card.
        transfers_without_card=True,
        # Verified 2026-09-16 against point.me and awardtravelfinder.com; only partners
        # both list are included. Contested (one source only): American AAdvantage,
        # Spirit, Virgin Red, Accor (3:2), Wyndham, Preferred Hotels (1:2).
        partners=[
            Partner("Aer Lingus AerClub", "airline", "1:1"),
            Partner("Air Canada Aeroplan", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("Alaska Atmos Rewards", "airline", "1:1"),
            Partner("Avianca LifeMiles", "airline", "1:1"),
            Partner("British Airways Executive Club", "airline", "1:1"),
            Partner("Cathay Pacific Asia Miles", "airline", "1:1"),
            Partner("Emirates Skywards", "airline", "1:1"),
            Partner("Etihad Guest", "airline", "1:1"),
            Partner("Iberia Plus", "airline", "1:1"),
            Partner("JAL Mileage Bank", "airline", "1:1"),
            Partner("Qatar Airways Privilege Club", "airline", "1:1"),
            Partner("Southwest Rapid Rewards", "airline", "1:1"),
            Partner("TAP Air Portugal Miles&Go", "airline", "1:1"),
            Partner("Turkish Airlines Miles&Smiles", "airline", "1:1"),
            Partner("United MileagePlus", "airline", "1:1"),
            Partner("Virgin Atlantic Flying Club", "airline", "1:1"),
            Partner("Hilton Honors", "hotel", "1:1"),
            Partner("IHG One Rewards", "hotel", "1:1"),
            Partner("Marriott Bonvoy", "hotel", "1:1"),
            Partner("World of Hyatt", "hotel", "1:1"),
        ],
    ),
    "usbank_points": Pool(
        key="usbank_points",
        currency_name="U.S. Bank Points",
        transferable=False,
        portal_rate_cents=1.0,  # up to 1.5 on Altitude Reserve via Real-Time Rewards
        fixed_value_note="No airline/hotel transfer partners — redeemable only at fixed value "
        "via Real-Time Rewards or the U.S. Bank rewards center.",
    ),
    "cashback": Pool(
        key="cashback",
        currency_name="Cash Back",
        transferable=False,
        portal_rate_cents=1.0,
        fixed_value_note="Straight cash back — no points, no transfer partners, no travel leverage.",
    ),
}


# ── Your cards ─────────────────────────────────────────────────────────────
CARDS: list[Card] = [
    Card("Bank of America Customized Cash Rewards", "Bank of America", "cashback", "held"),
    Card("US Bank Cash Plus", "U.S. Bank", "cashback", "held"),
    Card("US Bank Altitude Go", "U.S. Bank", "usbank_points", "held"),
    Card("Discover It", "Discover", "cashback", "held"),
    Card("Chase Freedom Flex", "Chase", "chase_ur", "held"),
    Card("Chase Freedom Unlimited", "Chase", "chase_ur", "held"),
    Card("Capital One Savor", "Capital One", "cashback", "held", notes="Earns cash back, not C1 miles"),
    Card("Capital One Quicksilver", "Capital One", "cashback", "held", notes="Earns cash back, not C1 miles"),
    Card("Wells Fargo Active Cash", "Wells Fargo", "cashback", "held"),
    Card("Wells Fargo Autograph", "Wells Fargo", "wf_rewards", "held", unlocks_transfer=True),
    Card("US Bank Altitude Connect", "U.S. Bank", "usbank_points", "held"),
    Card("Apple Card", "Goldman Sachs/Apple", "cashback", "held"),
    Card("Chase Sapphire Preferred", "Chase", "chase_ur", "held", unlocks_transfer=True),
    Card("Citi Custom Cash", "Citi", "citi_ty", "held"),
    Card("Citi Double Cash", "Citi", "citi_ty", "held"),
    Card("Bilt Mastercard", "Wells Fargo/Bilt", "bilt", "closed",
         notes="Closed; points kept in your Bilt account"),
    # Roadmap
    Card("Capital One Venture X", "Capital One", "cap1_miles", "planned", unlocks_transfer=True,
         notes="Repeatedly declined — reapply once inquiries/velocity cool down"),
    Card("Citi Strata Premier", "Citi", "citi_ty", "planned", unlocks_transfer=True),
    Card("Chase Sapphire Reserve", "Chase", "chase_ur", "planned", unlocks_transfer=True,
         notes="Product change target from Sapphire Preferred"),
]


def pool_is_active(pool_key: str) -> bool:
    """A pool is 'active' (transfer-eligible today) if you hold a card that unlocks it."""
    pool = POOLS[pool_key]
    if not pool.transferable:
        return False
    if pool.transfers_without_card:
        return True
    return any(c.pool_key == pool_key and c.status == "held" and c.unlocks_transfer for c in CARDS)


def cards_in_pool(pool_key: str, status: str | None = None) -> list[Card]:
    return [c for c in CARDS if c.pool_key == pool_key and (status is None or c.status == status)]


def find_partner_pools(program_query: str) -> list[tuple[Pool, Partner]]:
    """Case-insensitive substring search across all pools' partners."""
    q = program_query.strip().lower()
    if not q:
        return []
    matches = []
    for pool in POOLS.values():
        for partner in pool.partners:
            if q in partner.name.lower():
                matches.append((pool, partner))
    return matches


def all_partner_names() -> list[str]:
    names = {partner.name for pool in POOLS.values() for partner in pool.partners}
    return sorted(names)


def transfer_ratio_multiplier(ratio: str) -> float:
    """Convert a 'X:Y' ratio string into a multiplier: partner_units = pool_units * multiplier."""
    left, right = ratio.split(":")
    return float(right) / float(left)


def unique_partners(pool_key: str) -> list[str]:
    """Partners reachable from this pool but from no OTHER currently-active pool.

    These are the pool's 'escape hatches' — burning this pool's points costs you
    optionality on exactly these programs.
    """
    other_names = {
        pt.name
        for k, p in POOLS.items()
        if k != pool_key and pool_is_active(k)
        for pt in p.partners
    }
    return [pt.name for pt in POOLS[pool_key].partners if pt.name not in other_names]


def rank_funding_pools(
    matches: list[tuple[Pool, Partner]],
    points_required: float,
    balances: dict[str, int],
) -> list[dict]:
    """Rank active matched pools by which to burn first, best candidate first.

    Ordering: pools whose stored balance covers the transfer beat those that
    don't; among covered pools, burn the LEAST flexible pool (fewest unique
    partners) to preserve optionality; ties break on fewer points needed.
    """
    ranked = []
    for pool, partner in matches:
        if not pool_is_active(pool.key):
            continue
        pts_needed = points_required / transfer_ratio_multiplier(partner.ratio)
        balance = balances.get(pool.key, 0)
        uniques = unique_partners(pool.key)
        ranked.append(
            {
                "pool": pool,
                "partner": partner,
                "pts_needed": pts_needed,
                "balance": balance,
                "covered": balance >= pts_needed,
                "flexibility": len(uniques),
                "unique_partners": uniques,
            }
        )
    ranked.sort(key=lambda r: (not r["covered"], r["flexibility"], r["pts_needed"]))
    return ranked


def reachable_partner_names(active_pool_keys: set[str]) -> set[str]:
    """All partner programs reachable from the given set of pool keys."""
    return {pt.name for k in active_pool_keys for pt in POOLS[k].partners}
