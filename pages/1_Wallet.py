import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import ledger
from cards_data import CARDS, POOLS, cards_in_pool, pool_is_active

st.set_page_config(page_title="Wallet — PointsOptimizer", page_icon="💳", layout="centered")

st.title("Your Wallet")
st.caption(
    "Which of your cards feed which point currency, and where that currency can go. "
    "Transfer ratios and partner rosters change — verify on the issuer's site before booking."
)

st.divider()

held = [c for c in CARDS if c.status == "held"]
planned = [c for c in CARDS if c.status == "planned"]

st.subheader(f"Held cards ({len(held)})")
for card in held:
    note = f" — {card.notes}" if card.notes else ""
    st.write(f"- **{card.name}** ({card.issuer}) → {POOLS[card.pool_key].currency_name}{note}")

if planned:
    st.subheader(f"On your roadmap ({len(planned)})")
    for card in planned:
        note = f" — {card.notes}" if card.notes else ""
        st.write(f"- **{card.name}** ({card.issuer}) → {POOLS[card.pool_key].currency_name}{note}")

st.divider()
st.header("Point Balances")
st.caption("Saved locally to balances.json (gitignored) and used by the Flight Analyzer.")

balances = ledger.load_balances()
points_pools = [p for p in POOLS.values() if p.key != "cashback" and cards_in_pool(p.key, status="held")]

bal_cols = st.columns(len(points_pools))
new_balances = {}
for col, pool in zip(bal_cols, points_pools):
    with col:
        new_balances[pool.key] = st.number_input(
            pool.currency_name,
            min_value=0,
            value=balances.get(pool.key, 0),
            step=1000,
            key=f"bal_{pool.key}",
        )

if st.button("Save balances"):
    ledger.save_balances(new_balances)
    st.success("Balances saved.")

st.divider()
st.header("Point Pools & Transfer Partners")

for pool_key, pool in POOLS.items():
    cards_held = cards_in_pool(pool_key, status="held")
    cards_planned = cards_in_pool(pool_key, status="planned")
    if not cards_held and not cards_planned:
        continue

    active = pool_is_active(pool_key)
    icon = "🟢" if active else ("🟡" if pool.transferable else "⚪️")
    with st.expander(f"{icon} {pool.currency_name}", expanded=active and pool.transferable):
        holder_names = ", ".join(c.name for c in cards_held) or "none"
        st.write(f"**Earned by:** {holder_names}")

        if not pool.transferable:
            st.info(pool.fixed_value_note)
        elif active:
            st.success(
                f"Transfer-eligible now. Portal redemption ≈ {pool.portal_rate_cents:.2f}¢/point as a floor."
            )
        else:
            unlock_cards = [c.name for c in cards_planned if c.unlocks_transfer]
            if unlock_cards:
                st.warning(
                    f"Locked — you earn this currency but don't hold a card that unlocks transfer. "
                    f"Adding **{', '.join(unlock_cards)}** (on your roadmap) would unlock it."
                )
            else:
                st.warning("Locked — no held or planned card unlocks transfer for this currency.")

        if pool.transferable and pool.partners:
            st.write("**Transfer partners:**")
            airlines = [p for p in pool.partners if p.kind == "airline"]
            hotels = [p for p in pool.partners if p.kind == "hotel"]
            cols = st.columns(2)
            with cols[0]:
                st.markdown("*Airlines*")
                for p in airlines:
                    st.write(f"- {p.name} ({p.ratio})")
            with cols[1]:
                st.markdown("*Hotels*")
                for p in hotels:
                    st.write(f"- {p.name} ({p.ratio})")

st.divider()
st.caption(
    "🟢 transfer-eligible today · 🟡 earning but locked (need an unlocking card) · ⚪️ fixed-value only, no transfers"
)
