# app.py — per-session tokens (no disk), mixed-sheet validation, GTT SINGLE/OCO,
# optional SELL exact-match capping, WS linker (hold SELLs → fire on BUY fills) with tag-scoped matching,
# single Execute All button, auto-refreshing Live Positions with P&L kill switch.

import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from config import APP_TITLE  # no CREDS_PATH; no disk persistence
from services.auth import KiteAuth
from services.reader import read_orders_excel
from services.instruments import Instruments
from services.validation.validate import normalize_and_validate


from services.orders.pipeline import execute_bundle
from services.orders.placement import place_orders  # used by kill switch exit
from services.results import dataframe_to_excel_download
from services.ws import linker as ws_linker
from services.orders.exit import build_exit_intents_from_positions  # used by kill switch
from services import pnl_monitor
from models import OrderIntent


# =========================================================
# Setup
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# Strictly per-session token & kite client
if "access_token" not in st.session_state:
    st.session_state["access_token"] = None
if "kite" not in st.session_state:
    st.session_state["kite"] = None

access_token = st.session_state["access_token"]


# =========================================================
# Sidebar: credentials + mode (session-only; no disk)
# =========================================================
mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
api_key = st.sidebar.text_input("Kite API Key", value="")
api_secret = st.sidebar.text_input("Kite API Secret", type="password", value="")

# Sign-out clears only this session
if st.sidebar.button("Sign out / Clear token", use_container_width=True):
    st.session_state["access_token"] = None
    st.session_state["kite"] = None
    if pnl_monitor.is_running():
        pnl_monitor.stop()
    if ws_linker.is_running():
        ws_linker.stop()
        ws_linker.clear_deferred()
    st.sidebar.success("Session cleared")


# =========================================================
# Auth (per-session)
# =========================================================
auth = None
if api_key and api_secret:
    try:
        auth = KiteAuth(api_key, api_secret)  # no disk persistence inside KiteAuth
        if access_token:
            auth.kite.set_access_token(access_token)
            st.session_state["kite"] = auth.kite
            st.sidebar.success("Access token bound for this session")
    except Exception as e:
        st.sidebar.error(f"Auth init failed: {e}")


# =========================================================
# Login flow
# =========================================================
if st.sidebar.button("Get Login URL", disabled=(auth is None), use_container_width=True):
    try:
        st.sidebar.code(auth.login_url())
    except Exception as e:
        st.sidebar.error(f"Cannot generate login URL: {e}")

request_token = st.sidebar.text_input("Paste request_token")

if st.sidebar.button("Exchange token", disabled=(auth is None or not request_token), use_container_width=True):
    try:
        new_token = auth.exchange_request_token(request_token)
        st.session_state["access_token"] = new_token
        auth.kite.set_access_token(new_token)
        st.session_state["kite"] = auth.kite
        st.sidebar.success("New token generated (session-only)")
    except Exception as e:
        st.sidebar.error(f"Token exchange failed: {e}")

# Session test
if st.sidebar.button(
    "Test session",
    disabled=(st.session_state["kite"] is None or st.session_state["access_token"] is None),
    use_container_width=True
):
    try:
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        prof = st.session_state["kite"].profile()
        st.sidebar.success(f"Session OK: user_id={prof.get('user_id')}")
    except Exception as e:
        st.sidebar.error(f"Session test failed: {e}")


# =========================================================
# Upload Excel
# =========================================================
with st.expander("Excel format (required columns)"):
    st.write(
        "`symbol, exchange, txn_type, qty, order_type, price, trigger_price, "
        "product, validity, variety, disclosed_qty, tag`"
    )
    st.caption("Sheet name: 'Orders'")
    st.markdown("**GTT (single-leg)**: `gtt=YES, gtt_type=SINGLE, trigger_price, limit_price`")
    st.markdown("**GTT (OCO)**: `gtt=YES, gtt_type=OCO, trigger_price_1, limit_price_1, trigger_price_2, limit_price_2`")
    st.markdown("**WS Link groups (optional):** use `tag=link:<group>` to tie SELLs to specific BUYs.")

file = st.file_uploader("Upload Excel", type=["xlsx", "xls"])
raw_df = None
if file:
    try:
        raw_df = read_orders_excel(file)
        st.subheader("Preview")
        st.dataframe(raw_df.head(50), use_container_width=True)
    except Exception as e:
        st.error(f"Failed to read Excel: {e}")

st.markdown("---")


# =========================================================
# Validate
# =========================================================
validate_clicked = st.button("Validate orders", disabled=raw_df is None)
instruments = Instruments.load()

if validate_clicked and raw_df is not None:
    try:
        intents, vdf, errors = normalize_and_validate(raw_df, instruments)
        st.success(f"Validated {len(intents)} rows. Errors: {len(errors)}")

        st.session_state["validated_rows"] = [o.model_dump() for o in intents]

        if errors:
            edf = pd.DataFrame([{"row": i, "error": e} for i, e in errors])
            st.error("Some rows failed.")
            st.dataframe(edf, use_container_width=True)

        st.subheader("Validated (augmented)")
        st.dataframe(vdf, use_container_width=True)
    except Exception as e:
        st.error(f"Validation failed: {e}")


# =========================================================
# Actions after validation
# =========================================================
validated_rows = st.session_state.get("validated_rows", [])
validated_ok = len(validated_rows) > 0

# Optional SELL capping — exact match to actual available (legacy safety)
st.markdown("#### Optional: Auto-cap SELLs to what you actually own/have bought today")
auto_exact_match = st.checkbox("Enable auto-cap for SELL orders (holdings + today's BUY fills)", value=False)
strict_product = st.checkbox("Strict product matching (SELL MIS must match MIS pool)", value=True)
st.caption("When enabled, SELL quantities are capped to available pool per (exchange, symbol, product).")

# Linker (WebSocket) Options
st.markdown("#### Linker (WebSocket) Options")
link_ws = st.checkbox(
    "Hold SELLs and link to BUY fills via WebSocket (supports tag=link:<group>)",
    value=False
)
st.caption("Places BUYs now. Defers SELLs (regular & GTT). Fires SELLs automatically when cumulative BUY fills ≥ SELL qty for the same (exchange, symbol, group).")
linker_running = ws_linker.is_running()
st.text(f"Linker status: {'RUNNING' if linker_running else 'STOPPED'}")

if linker_running and st.button("Stop linker", use_container_width=True):
    ws_linker.stop()
    st.success("Linker stopped.")

execute_all_clicked = st.button(
    "Execute All (Regular + GTT SINGLE/OCO)",
    disabled=not validated_ok,
    use_container_width=True
)

# # Estimate margins (optional demo)
# if st.button("Estimate margins (demo)", disabled=not validated_ok, use_container_width=True):
#     intents = [OrderIntent(**d) for d in validated_rows]
#     mdf = estimate_notional(intents)
#     st.subheader("Margin Estimate (demo)")
#     st.dataframe(mdf, use_container_width=True)


# =========================================================
# Execute All
# =========================================================
if execute_all_clicked and validated_ok:
    live = (mode == "Live")
    try:
        intents = [OrderIntent(**d) for d in validated_rows]

        client = None
        if live:
            if not st.session_state["access_token"]:
                st.error("Please exchange your own token in this session.")
                st.stop()
            if st.session_state["kite"] is None:
                if auth is None:
                    st.error("Auth not initialized.")
                    st.stop()
                st.session_state["kite"] = auth.kite
            st.session_state["kite"].set_access_token(st.session_state["access_token"])
            prof = st.session_state["kite"].profile()
            st.info(f"Placing as user_id={prof.get('user_id')}")
            client = st.session_state["kite"]

        # If link_ws is enabled and live, SELLs are deferred to WS linker.
        execute_bundle(
            intents=intents,
            kite=client,
            live=live,
            enforce_exact_sell=auto_exact_match,
            link_sells_via_ws=(link_ws and live),
            api_key=api_key,
            access_token=st.session_state["access_token"] if live else None,
        )

        if live:
            st.success("Execution finished.")
        else:
            st.info("Dry-run only; nothing sent.")

    except Exception as e:
        st.error(f"Execution failed: {e}")


# =========================================================
# Live Positions (auto-refresh) + Kill Switch just below
# =========================================================
st.markdown("---")
st.markdown("### Live Positions (NRML)")

# Start/stop monitor depending on Live mode
if mode == "Live" and st.session_state.get("kite") is not None:
    if not pnl_monitor.is_running():
        pnl_monitor.start(st.session_state["kite"])
else:
    if pnl_monitor.is_running():
        pnl_monitor.stop()

# Auto-refresh this area every 2s without user click
st_autorefresh(interval=2000, key="pos_tick")

snap = pnl_monitor.get_snapshot()
err = snap.get("error")
if err:
    st.warning(f"Positions monitor error: {err}")

rows = snap.get("rows", [])
if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
else:
    st.info("No open NRML positions.")

# Aggregates
colA, colB, colC = st.columns(3)
colA.metric("Net P&L (₹)", f"{snap.get('net_pnl', 0.0):.2f}")
colB.metric("Profit sum (₹)", f"{snap.get('net_profit', 0.0):.2f}")
colC.metric("Loss sum (₹)", f"{snap.get('net_loss', 0.0):.2f}")

# Kill switch controls directly below the table (reuses existing exit flow; no extra exit button)
st.markdown("#### Global P&L Kill Switch")
ks_enabled = st.checkbox("Enable kill switch (auto-exit all NRML when thresholds are hit)", value=False)
tp_val = st.number_input("Take Profit (₹)", min_value=0.0, value=0.0, step=100.0)
sl_val = st.number_input("Stop Loss (₹)", min_value=0.0, value=0.0, step=100.0)
pnl_monitor.arm_kill_switch(ks_enabled, tp_val, sl_val)

# If tripped, perform a single exit using existing flow; stop linker and clear queues first
if mode == "Live" and ks_enabled and snap.get("tripped"):
    try:
        if ws_linker.is_running():
            ws_linker.stop()
        ws_linker.clear_deferred()

        if st.session_state.get("kite") is None or st.session_state.get("access_token") is None:
            st.error("No active session for exit.")
            st.stop()
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        client = st.session_state["kite"]

        intents = build_exit_intents_from_positions(client, symbols_filter=None)
        if intents:
            with st.status("Kill switch triggered: exiting all NRML positions…", expanded=True) as s:
                res_df = place_orders(intents, kite=client, live=True)
                s.update(state="complete")
            st.subheader("Kill Switch Exit — Results")
            st.dataframe(res_df, use_container_width=True)
            data, fname = dataframe_to_excel_download(res_df)
            st.download_button(
                "Download exit_results.xlsx",
                data=data,
                file_name=fname.replace("results", "exit_results"),
                use_container_width=True
            )
        else:
            st.info("Kill switch triggered, but no NRML positions to exit.")

        pnl_monitor.arm_kill_switch(False, None, None)
        st.success("Global exit completed and kill switch disarmed.")
    except Exception as e:
        st.error(f"Kill switch exit failed: {e}")


# =========================================================
# Debug
# =========================================================
st.markdown("---")
st.markdown("### Token Debug (session-scoped)")
st.json({
    "SESSION_STATE_ACCESS_TOKEN_SET": st.session_state.get("access_token") is not None,
    "KITE_BOUND": st.session_state.get("kite") is not None,
})
