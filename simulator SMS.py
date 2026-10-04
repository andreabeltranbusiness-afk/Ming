"""
Feature-phone simulator for demos. Run:  streamlit run simulator.py
Uses the SAME core.py as the real webhook, so what you demo is what a farmer would get.
"""
import pandas as pd
import streamlit as st
from core import SMS_MAX, USSD_MAX, Store, handle_sms, handle_ussd

st.set_page_config(page_title="Ming · feature phone", page_icon="📟", layout="centered")
st.markdown("""<style>
.lcd{background:#a9c28a;border:6px solid #3b4a2c;border-radius:10px;padding:14px 16px;font-family:monospace;
     font-size:17px;line-height:1.35;color:#1f2a14;white-space:pre-wrap;min-height:150px}
</style>""", unsafe_allow_html=True)

store = Store("ming_sim.db")
ss = st.session_state
ss.setdefault("path", None); ss.setdefault("screen", ""); ss.setdefault("limit", USSD_MAX); ss.setdefault("sms_log", [])
phone = st.sidebar.text_input("Simulated phone number", "+000000001")
st.sidebar.caption("Production hashes the number for language prefs; the raw number is stored only when the farmer replies '1 = send'.")

st.title("📟 Ming — feature-phone channel")
st.caption("No camera, no app, no data plan. Works on any handset via USSD (menu) or SMS (keywords).")
tab_u, tab_s, tab_q = st.tabs(["USSD  *123#", "SMS", "Technician queue"])

with tab_u:
    if st.button("📞 Dial *123#"):
        ss.path = []; ss.screen, end = handle_ussd("", phone, store)
    if ss.screen:
        st.markdown(f"<div class='lcd'>{ss.screen}</div>", unsafe_allow_html=True)
        st.caption(f"{len(ss.screen)}/{USSD_MAX} characters")
    if ss.path is not None:
        with st.form("reply", clear_on_submit=True):
            x = st.text_input("Your reply (type the number, then Send)")
            if st.form_submit_button("Send") and x.strip():
                ss.path.append(x.strip())
                ss.screen, end = handle_ussd("*".join(ss.path), phone, store)
                if end: ss.path = None
                st.rerun()
    elif ss.screen:
        st.info("Session ended. Dial again to restart.")

with tab_s:
    st.caption("Try: DX · DX 1 1 1 · PRICE · PRICE 1 6.5 · TECH · LANG ES · HELP")
    with st.form("sms", clear_on_submit=True):
        b = st.text_input("Text message to the Ming number")
        if st.form_submit_button("Send SMS") and b.strip():
            ss.sms_log += [("You", b), ("Ming", handle_sms(b, phone, store))]
    for who, m in reversed(ss.sms_log):
        st.markdown(f"**{who}:** {m}" + (f"  \n<sub>{len(m)}/{SMS_MAX} chars</sub>" if who == "Ming" else ""), unsafe_allow_html=True)

with tab_q:
    st.caption("What the extension worker / cooperative sees. High-priority (e.g. possible rust) first.")
    rows = store.queue()
    st.dataframe(pd.DataFrame(rows, columns=["id", "time", "phone", "channel", "kind", "priority", "payload", "status"]), use_container_width=True)
    i = st.number_input("Ticket id", min_value=1, step=1)
    if st.button("✅ Mark resolved (then delete the phone number per your retention policy)"):
        store.resolve(int(i)); st.rerun()
