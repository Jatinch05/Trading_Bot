# app.py — FULL, WORKING, PATCHED VERSION (Option A)
# Clean architecture, instance-based, Streamlit-safe

import streamlit as st
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from config import APP_TITLE
from models import OrderIntent

from services.auth import KiteAuth
from services.reader import read_orders_excel
from services.instruments import Instruments
from services.validation.validate import normalize_and_validate

from services.orders.pipeline import execute_bundle
from services.orders.exit import build_exit_intents_from_positions
from services.results import dataframe_to_excel_download

from services.ws.linker import OrderLinker
from services.ws.ws_manager import WSManager
from services.ws.gtt_watcher import GTTWatcher

from services import pnl_monitor


# =========================================================
# Page setup
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)


# =========================================================
# Session state (CRITICAL)
# =========================================================
DEFAULT_STATE = {
    "access_token": None,
    "kite": None,

    "validated_rows": [],
    "vdf_disp": None,
    "selected_rows": set(),

    # runtime objects
    "linker": None,
    "ws": None,
    "gtt": None,
}

for k, v in DEFAULT_STATE.items():
    st.session_state.setdefault(k, v)


def ensure_linker() -> OrderLinker:
    if st.session_state["linker"] is None:
        st.session_state["linker"] = OrderLinker()
    return st.session_state["linker"]


# =========================================================
# Sidebar – mode & auth
# =========================================================
mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
live_mode = mode == "Live"

api_key = st.sidebar.text_input("Kite API Key")
api_secret = st.sidebar.text_input("Kite API Secret", type="password")

pause_refresh = st.sidebar.checkbox(
    "Pause live auto-refresh (recommended while selecting rows)", value=True
)


# =========================================================
# Logout / clear
# =========================================================
if st.sidebar.button("Sign out / Clear session", use_container_width=True):
    for k in DEFAULT_STATE:
        st.session_state[k] = DEFAULT_STATE[k]

    try:
        if pnl_monitor.is_running():
            pnl_monitor.stop()
    except Exception:
        pass

    st.sidebar.success("Session cleared.")


# =========================================================
# Auth
# =========================================================
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

if st.sidebar.button("Get Login URL", disabled=auth is None):
    st.sidebar.code(auth.login_url())

request_token = st.sidebar.text_input("Paste request_token")
if st.sidebar.button("Exchange token", disabled=(auth is None or not request_token)):
    try:
        tok = auth.exchange_request_token(request_token)
        st.session_state["access_token"] = tok
        auth.kite.set_access_token(tok)
        st.session_state["kite"] = auth.kite
        st.sidebar.success("Token set (session-only).")
    except Exception as e:
        st.sidebar.error(f"Exchange failed: {e}")

if st.sidebar.button("Test session", disabled=(st.session_state["kite"] is None)):
    try:
        prof = st.session_state["kite"].profile()
        st.sidebar.success(f"user_id={prof.get('user_id')}")
    except Exception as e:
        st.sidebar.error(f"Session test failed: {e}")


# =========================================================
# Excel upload
# =========================================================
with st.expander("Excel format (required columns)"):
    st.code(
        """symbol, exchange, txn_type, qty, order_type, price, trigger_price,
product, validity, variety, disclosed_qty, tag,
gtt, gtt_type, gtt_trigger, gtt_limit,
gtt_trigger_1, gtt_limit_1, gtt_trigger_2, gtt_limit_2"""
    )
    st.caption("Use tag=link:<group> to link BUY/SELL automation.")

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


# =========================================================
# Validation + persistent selection
# =========================================================
validate_clicked = st.button("Validate Orders", disabled=(raw_df is None))
instruments = Instruments.load()


def render_selection_table():
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

    edited = st.data_editor(
        disp,
        hide_index=False,
        use_container_width=True,
        column_config={"select": st.column_config.CheckboxColumn("Select")},
        key="validated_editor",
    )

    st.session_state["vdf_disp"] = edited.copy()
    st.session_state["selected_rows"] = set(
        edited.index[edited["select"]].tolist()
    )


try:
    if validate_clicked and raw_df is not None:
        intents, vdf, errors = normalize_and_validate(raw_df, instruments)

        st.session_state["validated_rows"] = vdf.to_dict("records")
        st.session_state["vdf_disp"] = vdf.copy()
        st.session_state["selected_rows"] = set()

        st.success(f"Validated {len(intents)} rows.")
        if errors:
            st.error("Some rows failed.")
            st.dataframe(
                pd.DataFrame(errors, columns=["row", "error"]),
                use_container_width=True,
            )

        render_selection_table()

    elif st.session_state["vdf_disp"] is not None:
        render_selection_table()
    else:
        st.info("Upload a file and click Validate Orders.")

except Exception as e:
    st.error(f"Validation failed: {e}")

st.markdown("---")


# =========================================================
# Linker wiring (CORE)
# =========================================================
linker = ensure_linker()


def _release_sells(intents):
    client = st.session_state.get("kite")
    if not client:
        return
    # Place released sells directly; do not re-queue
    from services.orders.pipeline import execute_released_sells
    execute_released_sells(kite=client, sells=intents, live=True)


linker.set_release_callback(_release_sells)


# =========================================================
# Execution helpers
# =========================================================
def execute_rows(rows):
    client = None

    if live_mode:
        if not st.session_state["kite"]:
            st.error("No active Kite session.")
            return

        client = st.session_state["kite"]

        if st.session_state["ws"] is None:
            st.session_state["ws"] = WSManager(
                api_key,
                st.session_state["access_token"],
                linker,
            )
            st.session_state["ws"].start()

        if st.session_state["gtt"] is None:
            st.session_state["gtt"] = GTTWatcher(client)
            st.session_state["gtt"].bind_linker(linker)
            st.session_state["gtt"].start()

    intents = [OrderIntent(**r) for r in rows]

    if not live_mode:
        # Dry-run: just show what WOULD be placed
        results = [{
            "symbol": i.symbol,
            "txn_type": i.txn_type,
            "qty": i.qty,
            "order_type": i.order_type,
            "status": "DRY-RUN",
        } for i in intents]
    else:
        results = execute_bundle(
            kite=client,
            intents=intents,
            linker=linker,
        )

    df = pd.DataFrame(results)
    st.subheader("Execution Results")
    st.dataframe(df, use_container_width=True)

    data, fname = dataframe_to_excel_download(df)
    st.download_button("Download Results", data=data, file_name=fname)


# =========================================================
# Execute controls
# =========================================================
validated_ok = (
    st.session_state["vdf_disp"] is not None
    and len(st.session_state["vdf_disp"]) > 0
)

c1, c2 = st.columns(2)
exec_selected = c1.button("Execute Selected", disabled=not validated_ok)
exec_all = c2.button("Execute ALL", disabled=not validated_ok)

if exec_selected:
    sel = st.session_state["selected_rows"]
    if not sel:
        st.warning("No rows selected.")
    else:
        src = st.session_state["vdf_disp"]
        chosen = src.loc[list(sel)].drop(columns=["select"], errors="ignore")
        execute_rows(chosen.to_dict("records"))

if exec_all:
    src = st.session_state["vdf_disp"].drop(columns=["select"], errors="ignore")
    execute_rows(src.to_dict("records"))


# =========================================================
# Exit ALL
# =========================================================
if st.button("Exit ALL NRML Positions", disabled=not live_mode):
    try:
        client = st.session_state["kite"]
        intents = build_exit_intents_from_positions(client)
        if not intents:
            st.info("No NRML positions.")
        else:
            results = execute_bundle(
                kite=client,
                intents=intents,
                linker=linker,
            )
            st.dataframe(pd.DataFrame(results), use_container_width=True)
    except Exception as e:
        st.error(f"Exit ALL failed: {e}")


# =========================================================
# Live P&L + kill switch
# =========================================================
st.markdown("### Live NRML Positions")

if live_mode and st.session_state.get("kite"):
    if not pnl_monitor.is_running():
        pnl_monitor.start(st.session_state["kite"], live=True)
else:
    if pnl_monitor.is_running():
        pnl_monitor.stop()

if not pause_refresh:
    st_autorefresh(interval=2000, key="pos_tick")

snap = pnl_monitor.get_snapshot()
rows = snap.get("rows", [])

if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)
else:
    st.info("No open NRML positions.")

c1, c2, c3 = st.columns(3)
c1.metric("Net P&L", f"{snap.get('net_pnl', 0.0):.2f}")
c2.metric("Profit Σ", f"{snap.get('net_profit', 0.0):.2f}")
c3.metric("Loss Σ", f"{snap.get('net_loss', 0.0):.2f}")

ks_on = st.checkbox("Enable Kill Switch")
tp = st.number_input("Take Profit (₹)", min_value=0.0)
sl = st.number_input("Stop Loss (₹)", min_value=0.0)
pnl_monitor.arm_kill_switch(ks_on, tp, sl)


# =========================================================
# Debug panels
# =========================================================
st.markdown("---")
st.markdown("### Linker / Runtime Debug")

st.caption("Order linker snapshot")
st.json(linker.snapshot())

st.caption("GTT watcher")
if st.session_state["gtt"]:
    st.json(st.session_state["gtt"].snapshot())
else:
    st.warning("GTT Watcher not initialized. Execute orders in Live mode to start.")
