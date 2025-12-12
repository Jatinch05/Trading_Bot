# app.py — FINAL PATCHED VERSION (compatible with updated linker, watcher, pipeline, placement, exits, pnl_monitor)

import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from config import APP_TITLE
from services.auth import KiteAuth
from services.reader import read_orders_excel
from services.instruments import Instruments
from services.validation.validate import normalize_and_validate

from services.orders.pipeline import execute_bundle
from services.orders.exit import build_exit_intents_from_positions
from services.results import dataframe_to_excel_download

from services.ws import linker as ws_linker
from services.ws import ws_manager
from services.ws import gtt_watcher
from services import pnl_monitor

from models import OrderIntent

# =====================================================================
#  Page Setup
# =====================================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# =====================================================================
#  Session State
# =====================================================================
for key, default in (
    ("access_token", None),
    ("kite", None),
    ("validated_rows", []),
    ("vdf_disp", None),
    ("selected_rows", set()),
):
    if key not in st.session_state:
        st.session_state[key] = default


# =====================================================================
#  Sidebar
# =====================================================================
mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
api_key = st.sidebar.text_input("Kite API Key", value="")
api_secret = st.sidebar.text_input("Kite API Secret", type="password", value="")

pause_refresh = st.sidebar.checkbox(
    "Pause live auto-refresh (recommended while selecting rows)",
    value=True,
)

# Logout / Clear session
if st.sidebar.button("Sign out / Clear session", use_container_width=True):
    st.session_state["access_token"] = None
    st.session_state["kite"] = None
    st.session_state["validated_rows"] = []
    st.session_state["vdf_disp"] = None
    st.session_state["selected_rows"] = set()
    try:
        if pnl_monitor.is_running():
            pnl_monitor.stop()
        if ws_manager.is_running():
            ws_manager.stop()
        if ws_linker.is_running():
            ws_linker.stop()
    except Exception:
        pass
    st.sidebar.success("Session cleared.")


# =====================================================================
#  Auth
# =====================================================================
auth = None
if api_key and api_secret:
    try:
        auth = KiteAuth(api_key, api_secret)
        if st.session_state["access_token"]:
            auth.kite.set_access_token(st.session_state["access_token"])
            st.session_state["kite"] = auth.kite
            st.sidebar.success("Access token bound.")
    except Exception as e:
        st.sidebar.error(f"Auth init failed: {e}")

if st.sidebar.button("Get Login URL", use_container_width=True, disabled=auth is None):
    try:
        st.sidebar.code(auth.login_url())
    except Exception as e:
        st.sidebar.error(f"Login URL failed: {e}")

request_token = st.sidebar.text_input("Paste request_token")
if st.sidebar.button("Exchange token", use_container_width=True, disabled=(auth is None or not request_token)):
    try:
        tok = auth.exchange_request_token(request_token)
        st.session_state["access_token"] = tok
        auth.kite.set_access_token(tok)
        st.session_state["kite"] = auth.kite
        st.sidebar.success("Token set (session-only).")
    except Exception as e:
        st.sidebar.error(f"Exchange failed: {e}")

if st.sidebar.button("Test session", use_container_width=True, disabled=(st.session_state["kite"] is None)):
    try:
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        prof = st.session_state["kite"].profile()
        st.sidebar.success(f"user_id={prof.get('user_id')}")
    except Exception as e:
        st.sidebar.error(f"Session test failed: {e}")

# =====================================================================
#  Upload Excel
# =====================================================================
with st.expander("Excel format (required columns)"):
    st.code(
        """symbol, exchange, txn_type, qty, order_type, price, trigger_price,
product, validity, variety, disclosed_qty, tag,
gtt, gtt_type, gtt_trigger, gtt_limit,
gtt_trigger_1, gtt_limit_1, gtt_trigger_2, gtt_limit_2
""",
    )
    st.caption("Use tag=link:<group> to strictly pair SELLs with their BUY group.")

file = st.file_uploader("Upload Excel", type=["xlsx"])
raw_df = None
if file:
    try:
        raw_df = read_orders_excel(file)
        st.subheader("Preview")
        st.dataframe(raw_df.head(20), use_container_width=True)
    except Exception as e:
        st.error(f"Failed reading Excel: {e}")

st.markdown("---")

# =====================================================================
#  Validation + Persistent Row Selection
# =====================================================================
validate_clicked = st.button("Validate Orders", disabled=(raw_df is None))
instruments = Instruments.load()

def render_selection_table():
    st.subheader("Validated Orders — Select rows in the table")

    disp = st.session_state["vdf_disp"].copy()

    if "select" not in disp.columns:
        disp.insert(0, "select", False)

    if st.session_state["selected_rows"]:
        disp.loc[:, "select"] = False
        for i in st.session_state["selected_rows"]:
            if i in disp.index:
                disp.loc[i, "select"] = True

    c1, c2 = st.columns(2)
    if c1.button("Select all"):
        disp.loc[:, "select"] = True
    if c2.button("Clear all"):
        disp.loc[:, "select"] = False

    edited_df = st.data_editor(
        disp,
        hide_index=False,
        use_container_width=True,
        column_config={"select": st.column_config.CheckboxColumn("Select", help="Include this row")},
        key="validated_editor_persist",
    )

    st.session_state["vdf_disp"] = edited_df.copy()
    st.session_state["selected_rows"] = set(
        edited_df.index[edited_df["select"]].tolist()
    )

try:
    if validate_clicked and raw_df is not None:
        intents, vdf, errors = normalize_and_validate(raw_df, instruments)

        st.session_state["validated_rows"] = vdf.to_dict("records")
        st.session_state["vdf_disp"] = vdf.copy()
        st.session_state["selected_rows"] = set()

        st.success(f"Validated {len(intents)} rows. Errors: {len(errors)}")

        if errors:
            edf = pd.DataFrame([{"row": i, "error": e} for i, e in errors])
            st.error("Some rows failed.")
            st.dataframe(edf, use_container_width=True)

        render_selection_table()
    elif st.session_state["vdf_disp"] is not None and len(st.session_state["vdf_disp"]) > 0:
        render_selection_table()
    else:
        st.info("Upload a file and click ‘Validate Orders’ to continue.")

except Exception as e:
    st.error(f"Validation failed: {e}")

st.markdown("---")

# =====================================================================
#  Linker Controls
# =====================================================================
st.markdown("#### Linker (WS) Options")

enable_linking = st.checkbox(
    "Hold SELLs and link to BUY fills via WebSocket (tag=link:<group>)",
    value=True if mode == "Live" else False,
)

linker_running = ws_linker.is_running()
st.text(f"Linker status: {'RUNNING' if linker_running else 'STOPPED'}")

if linker_running and st.button("Stop Linker", use_container_width=True):
    ws_linker.stop()
    st.success("Linker stopped & cleared.")

# =====================================================================
#  Execute
# =====================================================================
validated_ok = st.session_state["vdf_disp"] is not None and len(st.session_state["vdf_disp"]) > 0

col1, col2, col3 = st.columns(3)
exec_selected = col1.button("Execute Selected", disabled=not validated_ok)
exec_all = col2.button("Execute ALL", disabled=not validated_ok)
estimate_btn = col3.button("Estimate Margins (demo)", disabled=not validated_ok)

exit_all_button = st.button("Exit ALL NRML Positions", disabled=(st.session_state["kite"] is None))

def _execute_rows(rows):
    live = mode == "Live"
    client = None

    if live:
        if not st.session_state["kite"]:
            st.error("No active Kite session.")
            return
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        client = st.session_state["kite"]

    intents = [OrderIntent(**r) for r in rows]

    if live and not ws_manager.is_running():
        if not st.session_state["access_token"]:
            st.error("No access_token in session.")
            return
        ws_manager.start(api_key, st.session_state["access_token"], ws_linker.credit_by_order_id)

    results = execute_bundle(
        intents=intents,
        kite=client,
        live=live,
        link_sells_via_ws=(enable_linking and live),
    )

    df = pd.DataFrame(results)
    st.subheader("Execution Results")
    st.dataframe(df, use_container_width=True)
    data, fname = dataframe_to_excel_download(df)
    st.download_button("Download Results", data=data, file_name=fname)

if exec_selected and validated_ok:
    sel = st.session_state["selected_rows"]
    if not sel:
        st.warning("No rows selected.")
    else:
        src = st.session_state["vdf_disp"]
        chosen = src.loc[list(sel)].drop(columns=["select"], errors="ignore")
        _execute_rows(chosen.to_dict("records"))

if exec_all and validated_ok:
    src = st.session_state["vdf_disp"].drop(columns=["select"], errors="ignore")
    _execute_rows(src.to_dict("records"))


# EXIT ALL
if exit_all_button:
    try:
        st.session_state["kite"].set_access_token(st.session_state["access_token"])
        client = st.session_state["kite"]
        intents = build_exit_intents_from_positions(client)
        if not intents:
            st.info("No NRML positions to exit.")
        else:
            results = execute_bundle(intents, kite=client, live=True, link_sells_via_ws=False)
            st.dataframe(pd.DataFrame(results), use_container_width=True)
    except Exception as e:
        st.error(f"Exit ALL failed: {e}")

st.markdown("---")

# =====================================================================
#  Live P&L + Kill Switch
# =====================================================================
st.markdown("### Live NRML Positions")

if mode == "Live" and st.session_state.get("kite"):
    if not pnl_monitor.is_running():
        pnl_monitor.start(st.session_state["kite"], live=True)
else:
    if pnl_monitor.is_running():
        pnl_monitor.stop()

if not pause_refresh:
    st_autorefresh(interval=2000, key="pos_tick")

snap = pnl_monitor.get_snapshot()

if snap.get("error"):
    st.warning(f"Monitor error: {snap['error']}")

rows = snap.get("rows", [])
if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)
else:
    st.info("No open NRML positions.")

c1, c2, c3 = st.columns(3)
c1.metric("Net P&L", f"{snap.get('net_pnl', 0.0):.2f}")
c2.metric("Profit Σ", f"{snap.get('net_profit', 0.0):.2f}")
c3.metric("Loss Σ", f"{snap.get('net_loss', 0.0):.2f}")

st.markdown("#### Global P&L Kill Switch")
ks_on = st.checkbox("Enable Kill Switch", key="ks_on_ui")
tp = st.number_input("Take Profit (₹)", min_value=0.0, key="tp_input")
sl = st.number_input("Stop Loss (₹)", min_value=0.0, key="sl_input")
pnl_monitor.arm_kill_switch(ks_on, tp, sl)

if snap.get("tripped") and ks_on:
    st.error("Kill Switch TRIGGERED — All positions exited.")
    if snap.get("exit_results"):
        st.dataframe(pd.DataFrame(snap["exit_results"]), use_container_width=True)


# =====================================================================
#  Linker + WS Debug Panels
# =====================================================================
st.markdown("---")
st.markdown("### Linker / WebSocket Debug")

if not pause_refresh:
    st_autorefresh(interval=2000, key="ws_dbg")

snap_l = ws_linker.snapshot()

colA, colB, colC = st.columns(3)

with colA:
    st.caption("Linker State")
    st.json({
        "running": snap_l.get("running"),
        "credits": list(snap_l.get("credits", {}).keys()),
        "queues": list(snap_l.get("queues", {}).keys()),
        "buy_registry": len(snap_l.get("buy_registry", {})),
        "gtt_triggers": len(snap_l.get("gtt_triggers", {})),
    })

with colB:
    credits = snap_l.get("credits", {})
    st.caption("Credits (per key)")
    if credits:
        st.dataframe(pd.DataFrame([{"key": k, "qty": v} for k, v in credits.items()]),
                     use_container_width=True, height=250)
    else:
        st.info("(no credits)")

with colC:
    queued = snap_l.get("queues", {})
    st.caption("Queued SELLs")
    if queued:
        view = []
        for k, sells in queued.items():
            view.append({
                "key": k,
                "queued_count": len(sells),
                "total_qty": sum(s.qty for s in sells),
            })
        st.dataframe(pd.DataFrame(view), use_container_width=True, height=250)
    else:
        st.info("(none queued)")

# Registered BUYs
reg = snap_l.get("buy_registry", {})
if reg:
    st.caption("Registered BUY order_ids")
    st.dataframe(pd.DataFrame([{"order_id": oid, "key": key} for oid, key in reg.items()]),
                 use_container_width=True, height=200)

# WS Events
events = ws_manager.events(limit=60)
st.caption("WS Order Updates (newest first)")
if events:
    ev_df = pd.DataFrame(events)
    keep_cols = [c for c in ["ts","event","order_id","symbol","status","filled","delta"] if c in ev_df.columns]
    st.dataframe(ev_df[keep_cols], use_container_width=True, height=240)
else:
    st.info("(No WS events yet)")

# Linker log
log_tail = snap_l.get("logs_tail", [])
st.caption("Linker Log (newest first)")
if log_tail:
    st.dataframe(pd.DataFrame(log_tail), use_container_width=True, height=260)
else:
    st.info("(no log entries)")

# GTT Watcher
st.caption("GTT Watcher Snapshot")
st.json(gtt_watcher.snapshot())

# =====================================================================
#  Dry-run Credit Simulator
# =====================================================================
st.markdown("#### Simulate BUY Credit (Dry-run only)")

if mode != "Live":
    keys = ws_linker.available_keys()
    if keys:
        colk, colq, colp, colbtn = st.columns([3,2,2,1])
        
        with colk:
            sel_key = st.selectbox("Key", options=keys)

        with colq:
            addq = st.number_input("Qty", min_value=1, step=1)

        with colp:
            persist = st.checkbox("Persist changes?", value=False)

        with colbtn:
            sim = st.button("Simulate")

        if sim:
            report = ws_linker.simulate_credit_offline_by_key(sel_key, addq, persist)
            df = pd.DataFrame([report]) if isinstance(report, dict) else pd.DataFrame(report)
            st.dataframe(df, use_container_width=True)

    else:
        st.info("No keys yet — execute orders with tag=link:<n> first.")
