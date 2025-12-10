import os
import streamlit as st
import pandas as pd

from config import APP_TITLE, TOKEN_PATH, CREDS_PATH
from services.storage import read_json, write_json
from services.auth import KiteAuth
from services.reader import read_orders_excel
from services.instruments import Instruments
from services.validate import normalize_and_validate
from services.margins import estimate_notional
from services.placer import place_orders
from services.results import dataframe_to_excel_download
from services.gtt import place_gtts_single, place_gtts_oco
from services.matcher import fetch_sellable_quantities, cap_sell_intents_by_sellable  # NEW
from models import OrderIntent


# =========================================================
# Setup
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# Load token (disk -> session) once per run
if "access_token" not in st.session_state:
    tok = read_json(TOKEN_PATH, {}).get("access_token")
    if tok:
        st.session_state["access_token"] = tok
access_token = st.session_state.get("access_token")


# =========================================================
# Sidebar: credentials + mode
# =========================================================
saved_creds = read_json(CREDS_PATH, {}) or {}
pref_key = saved_creds.get("api_key", "")
pref_secret = saved_creds.get("api_secret", "")

mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
api_key = st.sidebar.text_input("Kite API Key", value=pref_key)
api_secret = st.sidebar.text_input("Kite API Secret", type="password", value=pref_secret)

if api_key and api_secret and (api_key != pref_key or api_secret != pref_secret):
    write_json(CREDS_PATH, {"api_key": api_key, "api_secret": api_secret})
    st.sidebar.success("Credentials saved.")


# =========================================================
# Auth
# =========================================================
auth = None
if api_key and api_secret:
    try:
        auth = KiteAuth(api_key, api_secret, TOKEN_PATH)
        if access_token:
            auth.kite.set_access_token(access_token)
            st.sidebar.success("Access token loaded & bound")
    except Exception as e:
        st.sidebar.error(f"Auth init failed: {e}")


# =========================================================
# Login flow
# =========================================================
if st.sidebar.button("Get Login URL", disabled=(auth is None)):
    try:
        st.sidebar.code(auth.login_url())
    except Exception as e:
        st.sidebar.error(f"Cannot generate login URL: {e}")

request_token = st.sidebar.text_input("Paste request_token")

if st.sidebar.button("Exchange token", disabled=(auth is None or not request_token)):
    try:
        new_token = auth.exchange_request_token(request_token)
        st.session_state["access_token"] = new_token
        access_token = new_token
        auth.kite.set_access_token(new_token)
        st.sidebar.success("New token generated & saved")
    except Exception as e:
        st.sidebar.error(f"Token exchange failed: {e}")

# Session test
if st.sidebar.button("Test session", disabled=(auth is None or not access_token)):
    try:
        auth.kite.set_access_token(access_token)
        prof = auth.kite.profile()
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
    st.markdown("**GTT (single-leg)** requires: `gtt=YES, gtt_type=SINGLE, trigger_price, limit_price`")
    st.markdown("**GTT (OCO)** requires: `gtt=YES, gtt_type=OCO, trigger_price_1, limit_price_1, trigger_price_2, limit_price_2`")

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

colA, colB, colC, colD = st.columns(4)
estimate_clicked = colA.button("Estimate margins", disabled=not validated_ok)
place_clicked = colB.button("Place orders", disabled=(not validated_ok) or (mode == "Live" and not access_token))
create_gtt_single_clicked = colC.button("Create GTTs (single-leg)", disabled=not validated_ok)
create_gtt_oco_clicked = colD.button("Create GTTs (OCO)", disabled=not validated_ok)

# Optional auto-cap UI (NEW)
st.markdown("#### Optional: Auto-cap SELLs to what you actually own/have bought today")
auto_cap = st.checkbox("Enable auto-cap for SELL orders (holdings + today's BUY fills)", value=False)
strict_product = st.checkbox("Strict product matching (SELL MIS must match MIS pool)", value=True)
st.caption("If enabled, SELL quantities are capped to available pool per (exchange, symbol, product).")


# =========================================================
# Estimate margins (demo)
# =========================================================
if estimate_clicked and validated_ok:
    intents = [OrderIntent(**d) for d in validated_rows]
    mdf = estimate_notional(intents)
    st.subheader("Margin Estimate (demo)")
    st.dataframe(mdf, use_container_width=True)


# =========================================================
# Place Orders (market/limit/SL) with optional SELL auto-cap
# =========================================================
if place_clicked and validated_ok:
    live = (mode == "Live")
    try:
        intents = [OrderIntent(**d) for d in validated_rows]

        client = None
        if live:
            if not access_token:
                st.error("No active access token — login first.")
                st.stop()
            auth.kite.set_access_token(access_token)
            prof = auth.kite.profile()
            st.info(f"Placing as user_id={prof.get('user_id')}")
            client = auth.kite

        # --- NEW: Optional SELL capping ---
        adj_intents = intents
        cap_report = None
        if auto_cap:
            if live and client is not None:
                sellable = fetch_sellable_quantities(client)
                adj_intents, cap_report = cap_sell_intents_by_sellable(
                    intents, sellable, strict_product=strict_product
                )
                st.subheader("Sell Matching Report")
                st.dataframe(cap_report, use_container_width=True)
            else:
                st.warning("Auto-cap requires Live session to fetch holdings/positions. Proceeding without capping.")

        with st.status("Placing orders…", expanded=True) as s:
            res_df = place_orders(adj_intents, kite=client, live=live)
            s.update(state="complete")

        st.subheader("Results")
        st.dataframe(res_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(res_df)
        st.download_button("Download results.xlsx", data=data, file_name=fname)

        if live:
            st.success("Orders placed.")
        else:
            st.info("Dry-run only; nothing sent.")
    except Exception as e:
        st.error(f"Order placement failed: {e}")


# =========================================================
# Create GTTs (Single-leg; equities only)
# =========================================================
if create_gtt_single_clicked and validated_ok:
    try:
        if mode != "Live":
            st.warning("GTT creation requires Live mode.")
            st.stop()

        if not access_token:
            st.error("No active access token — login first.")
            st.stop()

        auth.kite.set_access_token(access_token)
        intents = [OrderIntent(**d) for d in validated_rows]

        with st.status("Creating GTTs (single)…", expanded=True) as s:
            gtt_df = place_gtts_single(intents, kite=auth.kite)
            s.update(state="complete")

        st.subheader("GTT Results (single-leg)")
        st.dataframe(gtt_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(gtt_df)
        st.download_button(
            "Download gtt_results_single.xlsx",
            data=data,
            file_name=fname.replace("results", "gtt_results_single"),
        )

        st.success("GTT routine finished")
    except Exception as e:
        st.error(f"GTT creation failed: {e}")


# =========================================================
# Create GTTs (OCO; two-leg)
# =========================================================
if create_gtt_oco_clicked and validated_ok:
    try:
        if mode != "Live":
            st.warning("GTT creation requires Live mode.")
            st.stop()

        if not access_token:
            st.error("No active access token — login first.")
            st.stop()

        auth.kite.set_access_token(access_token)
        intents = [OrderIntent(**d) for d in validated_rows]

        with st.status("Creating GTTs (OCO)…", expanded=True) as s:
            gtt_oco_df = place_gtts_oco(intents, kite=auth.kite)
            s.update(state="complete")

        st.subheader("GTT Results (OCO)")
        st.dataframe(gtt_oco_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(gtt_oco_df)
        st.download_button(
            "Download gtt_results_oco.xlsx",
            data=data,
            file_name=fname.replace("results", "gtt_results_oco"),
        )

        st.success("OCO GTT routine finished")
    except Exception as e:
        st.error(f"OCO GTT creation failed: {e}")


# =========================================================
# Debug
# =========================================================
st.markdown("### Token Debug")
st.json({
    "TOKEN_PATH": str(TOKEN_PATH),
    "TOKEN_FILE_EXISTS": TOKEN_PATH.exists(),
    "TOKEN_FILE_CONTENTS": read_json(TOKEN_PATH, {}),
    "SESSION_STATE_ACCESS_TOKEN": st.session_state.get("access_token"),
})
