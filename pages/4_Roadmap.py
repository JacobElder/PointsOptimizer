import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import ledger
from cards_data import CARDS, POOLS, pool_is_active, reachable_partner_names

st.set_page_config(page_title="Roadmap — PointsOptimizer", page_icon="🗺️", layout="centered")

st.title("Card Roadmap: What Would Each Unlock?")
st.caption(
    "For every card on your roadmap: which point pools it would activate, which partner "
    "programs become reachable that aren't today, and what happens to points you're "
    "already accumulating."
)

st.divider()

balances = ledger.load_balances()
current_active = {k for k in POOLS if pool_is_active(k)}
current_reach = reachable_partner_names(current_active)

planned = [c for c in CARDS if c.status == "planned"]
if not planned:
    st.info("No planned cards in cards_data.py — add cards with status='planned' to model them here.")
    st.stop()

for card in planned:
    pool = POOLS[card.pool_key]
    with st.container(border=True):
        st.subheader(card.name)
        if card.notes:
            st.caption(card.notes)

        activates_pool = (
            pool.transferable and card.unlocks_transfer and card.pool_key not in current_active
        )

        if activates_pool:
            hypothetical_reach = reachable_partner_names(current_active | {card.pool_key})
            new_partners = sorted(hypothetical_reach - current_reach)
            already_reachable = sorted(
                {pt.name for pt in pool.partners} - set(new_partners)
            )

            st.success(f"Unlocks transfers for **{pool.currency_name}** — currently locked.")

            stranded = balances.get(card.pool_key, 0)
            if stranded:
                st.write(
                    f"Your **{stranded:,} {pool.currency_name}** points would flip from "
                    f"~{pool.portal_rate_cents:.1f}¢ fixed value to transferable — "
                    f"potentially worth {'more' if pool.portal_rate_cents <= 1.0 else 'considerably more'} "
                    "at transfer-partner rates."
                )

            if new_partners:
                st.write(
                    f"**{len(new_partners)} partners no current pool can reach:** "
                    + ", ".join(new_partners)
                )
            if already_reachable:
                st.caption(
                    f"Redundant with current pools ({len(already_reachable)}): "
                    + ", ".join(already_reachable)
                )
            if new_partners and already_reachable:
                overlap_pct = len(already_reachable) / len(pool.partners) * 100
                st.caption(
                    f"Overlap with what you already have: {overlap_pct:.0f}% — "
                    + (
                        "mostly duplicative; the unique partners above are the real value."
                        if overlap_pct >= 60
                        else "meaningfully expands your reach."
                    )
                )
        elif card.pool_key in current_active:
            st.info(
                f"**{pool.currency_name}** is already unlocked by a card you hold. "
                "This card adds earning power and perks, not new transfer access."
            )
            if card.name == "Chase Sapphire Reserve":
                st.write(
                    "Portal floor improvement: UR points redeem at **1.5¢** in the Chase portal "
                    "with CSR vs **1.25¢** with your Sapphire Preferred. That raises the "
                    "guaranteed-value floor every hoard/redeem decision is measured against."
                )
        else:
            st.info("No transfer implications — this card doesn't unlock a transferable pool.")

st.divider()
st.caption(
    "Reachability computed from cards_data.py. Partner rosters change — verify before applying."
)
