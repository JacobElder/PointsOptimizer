import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import ledger
from cards_data import CARDS, POOLS, cards_in_pool, pool_is_active

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
if ledger.gist_enabled():
    st.caption("🔗 Synced: saved to your private balances Gist, shared by this site, the Mac app and the "
               "daily deal email. Update here and everything uses the new numbers.")
else:
    st.caption(
        "Saved only on the machine running the app (not in the public repo); on the hosted site they "
        "reset when it restarts. Add a GIST_TOKEN secret to sync one copy everywhere (see README)."
    )

balances = ledger.load_balances()
points_pools = [p for p in POOLS.values() if p.key != "cashback"
                and (cards_in_pool(p.key, status="held") or p.transfers_without_card)]

if not points_pools:
    st.info("No transferable point pools yet — add a card with status='held' in cards_data.py.")
bal_cols = st.columns(len(points_pools)) if points_pools else []
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
    synced = ledger.save_balances(new_balances)
    if synced:
        st.success("Balances saved and synced.")
    elif ledger.gist_enabled():
        st.error("Not saved: your balances Gist couldn't be read, so writing now could overwrite "
                 "the real numbers. Check GIST_TOKEN and reload.")
    else:
        st.success("Balances saved on this machine.")

st.subheader("Miles already in airline & hotel programs")
st.caption(
    "Points you've already transferred out (e.g. into JetBlue). The deal email and searches put "
    "deals you can book with these first."
)
import seats_aero  # noqa: E402

all_partners = sorted({pt.name for p in POOLS.values() for pt in p.partners}
                      | set(seats_aero.SOURCE_TO_PARTNER.values()))
program_balances = ledger.load_program_balances()
edited = st.data_editor(
    [{"Program": k, "Miles": v} for k, v in sorted(program_balances.items())] or [{"Program": None, "Miles": 0}],
    num_rows="dynamic",
    column_config={
        "Program": st.column_config.SelectboxColumn("Program", options=all_partners, required=False),
        "Miles": st.column_config.NumberColumn("Miles", min_value=0, step=500),
    },
    key="program_balances_editor",
)
if st.button("Save program balances"):
    synced = ledger.save_program_balances({r["Program"]: int(r["Miles"] or 0) for r in edited if r.get("Program")})
    if synced:
        st.success("Program balances saved and synced.")
    elif ledger.gist_enabled():
        st.error("Not saved: your balances Gist couldn't be read, so writing now could overwrite "
                 "the real numbers. Check GIST_TOKEN and reload.")
    else:
        st.success("Program balances saved on this machine.")

st.divider()
st.header("Point Pools & Transfer Partners")

for pool_key, pool in POOLS.items():
    cards_held = cards_in_pool(pool_key, status="held")
    cards_planned = cards_in_pool(pool_key, status="planned")
    if not cards_held and not cards_planned and not pool.transfers_without_card:
        continue

    active = pool_is_active(pool_key)
    icon = "🟢" if active else ("🟡" if pool.transferable else "⚪️")
    with st.expander(f"{icon} {pool.currency_name}", expanded=active and pool.transferable):
        holder_names = ", ".join(c.name for c in cards_held) or "none"
        st.write(f"**Earned by:** {holder_names}")
        if pool.transfers_without_card and not cards_held:
            st.caption("No open card needed: your membership balance can still be transferred.")

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
