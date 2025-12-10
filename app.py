# app.py — NRML-only, per-session tokens, row selection, exit-all/selected,
# exact-match SELLs for both regular and GTT

import streamlit as st
import pandas as pd

from config import APP_TITLE, CREDS_PATH
from services.storage import read_json, write_json
from services.auth import KiteAuth
from services.reader import read_orders_excel
from services.instruments import Instruments
from services.validate import normalize_and_validate
from services.margins import estimate_notional
from services.placer import place_orders
from services.results import dataframe_to_excel_download
from services.gtt import place_gtts_single, place_gtts_oco
from services.matcher import fetch_sellable_quantities, filter_sell_intents_exact
from models import OrderIntent

# ---------------- Setup ----------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

if "access_token" not in st.session_state:
    st.session_state["access_token"] = None
if "kite" not in st.session_state:
    st.session_state["kite"] = None
if "api_key_used_for_token" not in st.session_state:
    st.session_state["api_key_used_for_token"] = None

access_token = st.session_state["access_token"]

# ------------- Sidebar: creds + mode -------------
saved_creds = read_json(CREDS_PATH, {}) or {}
pref_key = saved_creds.get("api_key", "")
pref_secret = saved_creds.get("api_secret", "")

mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
api_key = st.sidebar.text_input("Kite API Key", value=pref_key)
api_secret = st.sidebar.text_input("Kite API Secret", type="password", value=pref_secret)

if api_key and api_secret and (api_key != pref_key or api_secret != pref_secret):
    write_json(CREDS_PATH, {"api_key": api_key, "api_secret": api_secret})
    st.sidebar.success("Credentials saved.")

if st.sidebar.button("Sign out / Clear token", use_container_width=True):
    st.session_state["access_token"] = None
    st.session_state["kite"] = None
    st.session_state["api_key_used_for_token"] = None
    st.sidebar.success("Session token cleared")

# ---------------- Auth ----------------
auth = None
if api_key and api_secret:
    try:
        auth = KiteAuth(api_key, api_secret)  # no disk token persistence
        if access_token:
            auth.kite.set_access_token(access_token)
            st.session_state["kite"] = auth.kite
            st.sidebar.success("Access token bound for this session")
    except Exception as e:
        st.sidebar.error(f"Auth init failed: {e}")

# ---------------- Login flow ----------------
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
        st.session_state["api_key_used_for_token"] = api_key  # pin api_key used
        st.sidebar.success("New token generated (session-only)")
    except Exception as e:
        st.sidebar.error(f"Token exchange failed: {e}")

if st.sidebar.button("Test session", disabled=(st.session_state["kite"] is None or st.session_state["access_token"] is None), use_container_width=True):
    try:
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        prof = st.session_state["kite"].profile()
        st.sidebar.success(f"Session OK: user_id={prof.get('user_id')}")
    except Exception as e:
        st.sidebar.error(f"Session test failed: {e}")

# ---------------- Upload Excel ----------------
with st.expander("Excel format (required columns)"):
    st.write("`symbol, exchange, txn_type, qty, order_type, price, trigger_price, product, validity, variety, disclosed_qty, tag`")
    st.caption("Sheet name: 'Orders'")
    st.markdown("**GTT (single-leg)**: `gtt=YES, gtt_type=SINGLE, trigger_price, limit_price`")
    st.markdown("**GTT (OCO)**: `gtt=YES, gtt_type=OCO, trigger_price_1, limit_price_1, trigger_price_2, limit_price_2`")

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

# ---------------- Validate ----------------
validate_clicked = st.button("Validate orders", disabled=raw_df is None)
instruments = Instruments.load()

if validate_clicked and raw_df is not None:
    try:
        intents, vdf, errors = normalize_and_validate(raw_df, instruments)
        st.success(f"Validated {len(intents)} rows. Errors: {len(errors)}")
        st.session_state["validated_rows"] = [o.model_dump() for o in intents]
        st.session_state["validated_df"] = vdf.copy()
        if errors:
            edf = pd.DataFrame([{"row": i, "error": e} for i, e in errors])
            st.error("Some rows failed.")
            st.dataframe(edf, use_container_width=True)
        st.subheader("Validated (augmented)")
        st.dataframe(vdf, use_container_width=True)
    except Exception as e:
        st.error(f"Validation failed: {e}")

# ---------------- Selection UI ----------------
validated_rows = st.session_state.get("validated_rows", [])
validated_df = st.session_state.get("validated_df", pd.DataFrame())
validated_ok = len(validated_rows) > 0

selected_df = None
if validated_ok and not validated_df.empty:
    st.markdown("#### Select rows to place")
    show_df = validated_df.copy()
    if "select" not in show_df.columns:
        show_df.insert(0, "select", False)
    selected_df = st.data_editor(
        show_df,
        use_container_width=True,
        disabled=[c for c in show_df.columns if c != "select"],
        key="order_selection_editor"
    )
    st.session_state["selected_mask"] = selected_df["select"].fillna(False).tolist()

# ---------------- Actions ----------------
col1, col2, col3, col4 = st.columns(4)
place_all_clicked      = col1.button("Place all orders", disabled=not validated_ok, use_container_width=True)
place_selected_clicked = col2.button("Place selected orders", disabled=not validated_ok, use_container_width=True)
exit_all_clicked       = col3.button("Exit all live orders", use_container_width=True)
exit_selected_clicked  = col4.button("Exit selected orders", use_container_width=True)

# ---------------- Helpers ----------------
def _ensure_live_client_or_stop():
    if st.session_state["kite"] is None or st.session_state["access_token"] is None:
        st.error("Live action requires a valid session token. Exchange your token first.")
        st.stop()
    if st.session_state.get("api_key_used_for_token") != api_key:
        st.error("API key changed after token exchange. Please exchange a new token.")
        st.stop()
    st.session_state["kite"].set_access_token(st.session_state["access_token"])
    try:
        prof = st.session_state["kite"].profile()
    except Exception as e:
        st.error(f"Session invalid: {e}. Please exchange a new token.")
        st.stop()
    return st.session_state["kite"], prof

def _split_regular_gtt(intents):
    regular = [i for i in intents if (i.gtt or "").upper() != "YES"]
    gtts    = [i for i in intents if (i.gtt or "").upper() == "YES"]
    return regular, gtts

def _build_exit_intents_from_positions(client, symbols_filter=None):
    intents_out = []
    try:
        pos = client.positions() or {}
        allpos = (pos.get("net") or []) + (pos.get("day") or [])
    except Exception:
        allpos = []

    seen = set()
    for p in allpos:
        exch = str(p.get("exchange") or "").upper()
        sym  = str(p.get("tradingsymbol") or "").upper()
        prod = str(p.get("product") or "").upper()
        netq = int(p.get("quantity") or p.get("net_quantity") or 0)
        key = (exch, sym, prod)
        if key in seen:
            continue
        seen.add(key)

        if prod != "NRML":
            continue
        if symbols_filter and sym not in symbols_filter:
            continue
        if netq == 0:
            continue

        txn = "SELL" if netq > 0 else "BUY"
        qty = abs(int(netq))

        intents_out.append(OrderIntent(
            symbol=sym,
            exchange=exch,
            txn_type=txn,
            qty=qty,
            order_type="MARKET",
            price=None,
            trigger_price=None,
            product="NRML",
            validity="DAY",
            variety="regular",
            disclosed_qty=None,
            tag="EXIT_ALL" if not symbols_filter else "EXIT_SEL",
            gtt="",
            gtt_type=""
        ))
    return intents_out

# ---------------- Placement flow ----------------
def _place_bundle(intents, client, live):
    """
    Pipeline: exact-match SELLs (regular + GTT) -> split -> place.
    """
    # Enforce exact-match SELLs using current NRML positions (live only)
    if live and client is not None:
        sellable = fetch_sellable_quantities(client)
        intents, exact_report = filter_sell_intents_exact(intents, sellable)
        st.subheader("Sell Exact-Match Report")
        if exact_report is not None and not exact_report.empty:
            st.dataframe(exact_report, use_container_width=True)
        else:
            st.info("No SELL rows in this batch.")

    regular_intents, gtt_intents = _split_regular_gtt(intents)

    # 1) Place REGULAR orders
    reg_res_df = pd.DataFrame()
    if regular_intents:
        with st.status("Placing regular orders…", expanded=True) as s:
            reg_res_df = place_orders(regular_intents, kite=client, live=live)
            s.update(state="complete")

    # 2) Create GTTs (SINGLE & OCO)
    gtt_single_df = pd.DataFrame()
    gtt_oco_df    = pd.DataFrame()
    if gtt_intents and live:
        with st.status("Creating GTTs…", expanded=True) as s2:
            gtt_single_df = place_gtts_single(gtt_intents, kite=client)
            gtt_oco_df = place_gtts_oco(gtt_intents, kite=client)
            s2.update(state="complete")
    elif gtt_intents and not live:
        st.info("GTT creation is skipped in Dry-run.")

    # Present results
    st.subheader("Results — Regular Orders")
    if not reg_res_df.empty:
        st.dataframe(reg_res_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(reg_res_df)
        st.download_button("Download results_regular.xlsx", data=data, file_name=fname, use_container_width=True)
    else:
        st.info("No regular orders to place.")

    st.subheader("Results — GTT (Single-leg)")
    if not gtt_single_df.empty:
        st.dataframe(gtt_single_df, use_container_width=True)
        data_s, fname_s = dataframe_to_excel_download(gtt_single_df)
        st.download_button("Download gtt_results_single.xlsx", data=data_s, file_name=fname_s.replace("results", "gtt_results_single"), use_container_width=True)
    else:
        st.info("No SINGLE GTTs to create.")

    st.subheader("Results — GTT (OCO)")
    if not gtt_oco_df.empty:
        st.dataframe(gtt_oco_df, use_container_width=True)
        data_o, fname_o = dataframe_to_excel_download(gtt_oco_df)
        st.download_button("Download gtt_results_oco.xlsx", data=data_o, file_name=fname_o.replace("results", "gtt_results_oco"), use_container_width=True)
    else:
        st.info("No OCO GTTs to create.")

# ---------------- Actions ----------------
if place_all_clicked and validated_ok:
    live = (mode == "Live")
    try:
        intents = [OrderIntent(**d) for d in validated_rows]
        client = None
        if live:
            client, prof = _ensure_live_client_or_stop()
            st.info(f"Placing as user_id={prof.get('user_id')}")
        _place_bundle(intents, client, live)
        st.success("Placement finished." if live else "Dry-run finished.")
    except Exception as e:
        st.error(f"Placement failed: {e}")

if place_selected_clicked and validated_ok:
    live = (mode == "Live")
    try:
        mask = st.session_state.get("selected_mask") or []
        if not any(mask):
            st.warning("No rows selected.")
            st.stop()
        selected_dicts = [d for d, m in zip(validated_rows, mask) if m]
        intents = [OrderIntent(**d) for d in selected_dicts]
        client = None
        if live:
            client, prof = _ensure_live_client_or_stop()
            st.info(f"Placing (selected) as user_id={prof.get('user_id')}")
        _place_bundle(intents, client, live)
        st.success("Selected placement finished." if live else "Selected dry-run finished.")
    except Exception as e:
        st.error(f"Selected placement failed: {e}")

# ---------------- Exit flows ----------------
st.markdown("---")
st.markdown("### Live Positions (NRML)")
if st.button("Refresh positions", use_container_width=True):
    try:
        client, _ = _ensure_live_client_or_stop()
        pos = client.positions() or {}
        net = pos.get("net") or []
        rows = []
        for p in net:
            if str(p.get("product") or "").upper() != "NRML":
                continue
            qty = int(p.get("quantity") or p.get("net_quantity") or 0)
            if qty == 0:
                continue
            rows.append({
                "exchange": str(p.get("exchange") or "").upper(),
                "symbol": str(p.get("tradingsymbol") or "").upper(),
                "product": "NRML",
                "net_qty": qty,
                "pnl": p.get("pnl"),
            })
        pdf = pd.DataFrame(rows)
        if pdf.empty:
            st.info("No open NRML positions.")
        else:
            st.dataframe(pdf, use_container_width=True)
            st.session_state["positions_symbols"] = sorted({r["symbol"] for r in rows})
    except Exception as e:
        st.error(f"Failed to fetch positions: {e}")

symbols_available = st.session_state.get("positions_symbols", [])
sel_symbols = st.multiselect(
    "Select symbols to exit (square-off NRML positions)",
    options=symbols_available,
    default=[],
)

if exit_all_clicked:
    try:
        client, _ = _ensure_live_client_or_stop()
        intents = _build_exit_intents_from_positions(client, symbols_filter=None)
        if not intents:
            st.info("No NRML positions to exit.")
            st.stop()
        with st.status("Exiting all NRML positions…", expanded=True) as s:
            res_df = place_orders(intents, kite=client, live=True)
            s.update(state="complete")
        st.subheader("Exit All — Results")
        st.dataframe(res_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(res_df)
        st.download_button("Download exit_all_results.xlsx", data=data, file_name=fname.replace("results", "exit_all_results"), use_container_width=True)
        st.success("Exit all finished.")
    except Exception as e:
        st.error(f"Exit all failed: {e}")

if exit_selected_clicked:
    try:
        if not sel_symbols:
            st.warning("No symbols selected to exit. Use Refresh positions and pick symbols.")
            st.stop()
        client, _ = _ensure_live_client_or_stop()
        intents = _build_exit_intents_from_positions(client, symbols_filter=set(sel_symbols))
        if not intents:
            st.info("No matching NRML positions for selected symbols.")
            st.stop()
        with st.status("Exiting selected NRML positions…", expanded=True) as s:
            res_df = place_orders(intents, kite=client, live=True)
            s.update(state="complete")
        st.subheader("Exit Selected — Results")
        st.dataframe(res_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(res_df)
        st.download_button("Download exit_selected_results.xlsx", data=data, file_name=fname.replace("results", "exit_selected_results"), use_container_width=True)
        st.success("Exit selected finished.")
    except Exception as e:
        st.error(f"Exit selected failed: {e}")

# ---------------- Optional: margins demo ----------------
if validated_ok and st.button("Estimate margins (demo)", use_container_width=True):
    intents = [OrderIntent(**d) for d in validated_rows]
    mdf = estimate_notional(intents)
    st.subheader("Margin Estimate (demo)")
    st.dataframe(mdf, use_container_width=True)

# ---------------- Debug ----------------
st.markdown("### Session Debug")
st.json({"SESSION_STATE_ACCESS_TOKEN_PRESENT": st.session_state.get("access_token") is not None})
