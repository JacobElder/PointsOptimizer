"""
Static reference data: your card portfolio, the point/mile pools they feed,
and each pool's transfer partners.

Transfer ratios and partner rosters change without notice — treat these as a
starting point and confirm current terms on the issuer's site before booking.
"""

from dataclasses import dataclass, field


@dataclass
class Card:
    name: str
    issuer: str
    pool_key: str  # which POOL this card's spend accrues to ("" if none)
    status: str  # "held" or "planned"
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


# ── Pools ──────────────────────────────────────────────────────────────────
POOLS: dict[str, Pool] = {
    "chase_ur": Pool(
        key="chase_ur",
        currency_name="Chase Ultimate Rewards",
        transferable=True,
        portal_rate_cents=1.25,  # 1.5 with Sapphire Reserve
        partners=[
            Partner("Aer Lingus AerClub", "airline", "1:1"),
            Partner("Air Canada Aeroplan", "airline", "1:1"),
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("British Airways Executive Club", "airline", "1:1"),
            Partner("Emirates Skywards", "airline", "1:1"),
            Partner("Iberia Plus", "airline", "1:1"),
            Partner("JetBlue TrueBlue", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
            Partner("Southwest Rapid Rewards", "airline", "1:1"),
            Partner("United MileagePlus", "airline", "1:1"),
            Partner("Virgin Atlantic Flying Club", "airline", "1:1"),
            Partner("IHG One Rewards", "hotel", "1:1"),
            Partner("Marriott Bonvoy", "hotel", "1:1"),
            Partner("World of Hyatt", "hotel", "1:1"),
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
            Partner("Emirates Skywards", "airline", "1:1"),
            Partner("Etihad Guest", "airline", "1:1"),
            Partner("EVA Air Infinity MileageLands", "airline", "2:1.5"),
            Partner("Finnair Plus", "airline", "1:1"),
            Partner("Qantas Frequent Flyer", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
            Partner("TAP Air Portugal Miles&Go", "airline", "2:1.5"),
            Partner("Turkish Airlines Miles&Smiles", "airline", "1:1"),
            Partner("Virgin Red", "airline", "1:1"),
            Partner("Wyndham Rewards", "hotel", "1:1"),
        ],
    ),
    "citi_ty": Pool(
        key="citi_ty",
        currency_name="Citi ThankYou Points",
        transferable=True,
        portal_rate_cents=1.0,  # 1.25 with a premium card via the portal
        partners=[
            Partner("Air France-KLM Flying Blue", "airline", "1:1"),
            Partner("Avianca LifeMiles", "airline", "1:1"),
            Partner("Cathay Pacific Asia Miles", "airline", "1:1"),
            Partner("Choice Privileges", "hotel", "1:1"),
            Partner("EVA Air Infinity MileageLands", "airline", "1:1"),
            Partner("JetBlue TrueBlue", "airline", "1:1"),
            Partner("Malaysia Airlines Enrich", "airline", "1:1"),
            Partner("Qantas Frequent Flyer", "airline", "1:1"),
            Partner("Qatar Airways Privilege Club", "airline", "1:1"),
            Partner("Singapore KrisFlyer", "airline", "1:1"),
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
    Card("US Bank Cash Plus", "U.S. Bank", "cashback", "held", notes="Listed twice — two accounts?"),
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
