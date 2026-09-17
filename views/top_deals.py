import streamlit as st

import deal_finder
import digest_view
import seats_aero

st.title("🏆 Top Deals")
st.caption(
    "The best award deals across your routes and watchlist, valued against live Google Flights fares. "
    "A full scan runs automatically every morning at 7am ET and emails you new standouts; "
    "scan now for fresher results."
)

digest = digest_view.load_digest()
if digest:
    st.caption(f"Last scan: {digest.get('generated_at', '?').replace('T', ' ').replace('Z', ' UTC')}")

with st.expander("🔄 Scan now", expanded=not digest):
    if not seats_aero.is_configured():
        st.warning("Needs SEATS_AERO_API_KEY in secrets.")
    else:
        mode = st.radio(
            "Scan type",
            ["Quick (~4 min): top deals and book-now, no watchlist",
             "Full (~9 min): everything, including every watchlist destination"],
            label_visibility="collapsed",
        )
        st.caption("Uses about 150–200 of your 1,000 daily seats.aero calls. Cash prices are free. "
                   "No email is sent from here.")
        if st.button("Scan now", type="primary"):
            quick = mode.startswith("Quick")
            with st.status("Scanning…", expanded=True) as status:
                try:
                    digest = deal_finder.run(
                        max_lookups=60 if quick else deal_finder.DEFAULT_MAX_LOOKUPS,
                        top=deal_finder.DEFAULT_TOP,
                        send_email=False,
                        log=status.write,
                        max_watch_lookups=0 if quick else deal_finder.DEFAULT_MAX_WATCH_LOOKUPS,
                        use_watchlist=not quick,
                    )
                    status.update(label="Scan complete", state="complete", expanded=False)
                except Exception as e:  # show, don't crash the page
                    status.update(label=f"Scan failed: {e}", state="error")

st.divider()
digest_view.render_digest(digest)
