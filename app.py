# app.py - Clean, production-ready Streamlit app
# Architecture: Auth → Upload → Validate → Execute → Monitor → Debug

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
# PAGE SETUP
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)


# =========================================================
# SESSION STATE
# =========================================================
DEFAULT_STATE = {
    "access_token": None,
    "kite": None,
    "validated_rows": [],
    "vdf_disp": None,
    "selected_rows": set(),
    "linker": None,
    "ws": None,
    "gtt": None,
}

for k, v in DEFAULT_STATE.items():
    st.session_state.setdefault(k, v)


# =========================================================
# AUTO-REFRESH (page-wide)
# =========================================================
# Allow pausing auto-refresh to avoid flicker during auth/token exchange
pause_refresh = st.sidebar.checkbox(
    "Pause auto-refresh",
    value=True,
    help="Disable periodic reruns to avoid flicker while authenticating"
)

if not pause_refresh:
    st_autorefresh(interval=2000, key="page_refresh")


# =========================================================
# HELPERS
# =========================================================
def ensure_linker() -> OrderLinker:
    """Ensure linker exists and has release callback."""
    if st.session_state["linker"] is None:
        st.session_state["linker"] = OrderLinker()
        st.session_state["linker"].set_release_callback(_on_sells_released)
    return st.session_state["linker"]


def ensure_gtt_watcher(kite, linker):
    """Ensure GTT watcher is initialized and running."""
    if st.session_state["gtt"] is None:
        st.session_state["gtt"] = GTTWatcher(kite)
        st.session_state["gtt"].bind_linker(linker)
        st.session_state["gtt"].start()
    elif not st.session_state["gtt"].running:
        st.session_state["gtt"].start()
    return st.session_state["gtt"]


def ensure_ws(kite, linker):
    """Ensure WebSocket is initialized and running."""
    if st.session_state["ws"] is None:
        st.session_state["ws"] = WSManager(
            api_key=st.session_state.get("api_key"),
            access_token=st.session_state["access_token"],
            linker=linker,
        )
        st.session_state["ws"].start()
    return st.session_state["ws"]


def _on_sells_released(sells: list):
    """Callback when linker releases SELLs after BUY fills."""
    print(f"[APP] Release callback triggered: {len(sells)} SELLs to place")
    client = st.session_state.get("kite")
    if not client:
        print("[APP] ❌ No Kite client, cannot place SELLs")
        return
    
    from services.orders.pipeline import execute_released_sells
    try:
        execute_released_sells(kite=client, sells=sells, live=True)
        print(f"[APP] ✅ Released {len(sells)} SELLs via callback")
    except Exception as e:
        print(f"[APP] ❌ Release failed: {e}")


# =========================================================
# SECTION 1: AUTHENTICATION (Sidebar)
# =========================================================
st.sidebar.markdown("## Authentication")

mode = st.sidebar.radio("Run mode", ["Dry-run (no orders)", "Live"], index=0)
live_mode = mode == "Live"

api_key = st.sidebar.text_input("Kite API Key", key="api_key_input")
api_secret = st.sidebar.text_input("Kite API Secret", type="password", key="api_secret_input")

# Store in session for later use
st.session_state["api_key"] = api_key

auth = None
if api_key and api_secret:
    try:
        auth = KiteAuth(api_key, api_secret)
        if st.session_state["access_token"]:
            auth.kite.set_access_token(st.session_state["access_token"])
            st.session_state["kite"] = auth.kite
            st.sidebar.success("✅ Token bound")
    except Exception as e:
        st.sidebar.error(f"Auth init failed: {e}")

if st.sidebar.button("Get Login URL", disabled=(auth is None)):
    st.sidebar.code(auth.login_url())

request_token = st.sidebar.text_input("Paste request_token")
if st.sidebar.button("Exchange Token", disabled=(auth is None or not request_token)):
    try:
        tok = auth.exchange_request_token(request_token)
        st.session_state["access_token"] = tok
        auth.kite.set_access_token(tok)
        st.session_state["kite"] = auth.kite
        st.sidebar.success("✅ Token exchanged (session-only)")
    except Exception as e:
        st.sidebar.error(f"Exchange failed: {e}")

if st.sidebar.button("Test Session", disabled=(st.session_state["kite"] is None)):
    try:
        prof = st.session_state["kite"].profile()
        st.sidebar.success(f"user_id={prof.get('user_id')}")
    except Exception as e:
        st.sidebar.error(f"Session test failed: {e}")

# Logout button
st.sidebar.markdown("---")
if st.sidebar.button("🚪 Sign Out / Clear Session", use_container_width=True):
    for k in DEFAULT_STATE:
        st.session_state[k] = DEFAULT_STATE[k]
    if pnl_monitor.is_running():
        pnl_monitor.stop()
    st.sidebar.success("Session cleared")


# =========================================================
# SECTION 2: ORDER EXECUTION (Main)
# =========================================================
if st.session_state["kite"] is None:
    st.warning("⚠️ Please authenticate first (see sidebar)")
    st.stop()

client = st.session_state["kite"]
linker = ensure_linker()

# Ensure runtime services are initialized in LIVE mode
if live_mode:
    ensure_gtt_watcher(client, linker)
    ensure_ws(client, linker)

st.markdown("## Order Execution")

# Excel upload
with st.expander("📋 Expected columns", expanded=False):
    st.code(
        """symbol, exchange, txn_type, qty, order_type, price, trigger_price,
product, validity, variety, disclosed_qty, tag,
gtt, gtt_type, gtt_trigger, gtt_limit, gtt_trigger_1, gtt_limit_1, gtt_trigger_2, gtt_limit_2"""
    )
    st.caption("Use tag=link:<group> to link BUY/SELL automation. Example: tag=link:1")

file = st.file_uploader("Upload Excel", type=["xlsx"])
raw_df = None

if file:
    raw_df = pd.read_excel(file)
    st.dataframe(raw_df.head(20), width="stretch")
    
    # Validate
    if st.button("✓ Validate Rows"):
        try:
            # Validate without instruments (as in previous version)
            intents, errors = normalize_and_validate(raw_df)
            st.session_state["validated_rows"] = intents
            
            if not errors:
                st.success(f"✅ All {len(intents)} rows valid")
                st.session_state["vdf_disp"] = raw_df.copy()
            else:
                st.error(f"❌ {len(errors)} rows failed validation")
                st.dataframe(
                    pd.DataFrame(errors, columns=["row", "error"]),
                    width="stretch",
                )
        except Exception as e:
            st.error(f"Validation error: {e}")

# Row selection
if st.session_state.get("vdf_disp") is not None:
    st.markdown("### Select Rows to Execute")
    
    disp = st.session_state["vdf_disp"].copy()
    # Add select column if it doesn't exist
    if "select" not in disp.columns:
        disp.insert(0, "select", False)
    
    edited = st.data_editor(
        disp,
        hide_index=False,
        width="stretch",
        column_config={"select": st.column_config.CheckboxColumn("Select")},
        key="order_editor",
    )
    
    st.session_state["vdf_disp"] = edited.copy()
    st.session_state["selected_rows"] = set(edited[edited["select"]].index)

# Execute
if st.button("🚀 Execute", disabled=(len(st.session_state["selected_rows"]) == 0)):
    rows = [
        st.session_state["validated_rows"][i].__dict__
        for i in st.session_state["selected_rows"]
    ]
    intents = [OrderIntent(**r) for r in rows]
    
    if not live_mode:
        # Dry-run
        results = [
            {
                "order_id": None,
                "symbol": i.symbol,
                "txn_type": i.txn_type,
                "qty": i.qty,
                "status": "DRY-RUN",
            }
            for i in intents
        ]
    else:
        # Live execution
        results = execute_bundle(kite=client, intents=intents, linker=linker, live=True)
    
    st.subheader("Execution Results")
    st.dataframe(pd.DataFrame(results), width="stretch")


# =========================================================
# SECTION 3: LIVE MONITORING
# =========================================================
if live_mode and st.session_state["kite"]:
    st.markdown("---")
    st.markdown("## 📊 Live Monitoring")
    
    # Live orders
    st.markdown("### Orders")
    try:
        orders = st.session_state["kite"].orders()
        if orders:
            order_df = pd.DataFrame(orders)
            display_cols = [
                "order_id", "tradingsymbol", "transaction_type", "quantity",
                "filled_quantity", "status", "order_type", "product", "order_timestamp",
            ]
            available_cols = [c for c in display_cols if c in order_df.columns]
            st.dataframe(order_df[available_cols], width="stretch", height=300)
        else:
            st.info("No orders")
    except Exception as e:
        st.error(f"Failed to fetch orders: {e}")
    
    # P&L
    st.markdown("### Positions & P&L")
    if not pnl_monitor.is_running():
        pnl_monitor.start(client, live=True)
    
    snap = pnl_monitor.get_snapshot()
    rows = snap.get("rows", [])
    
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", height=300)
    else:
        st.info("No open NRML positions")
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Net P&L", f"₹{snap.get('net_pnl', 0.0):.2f}")
    c2.metric("Profit Σ", f"₹{snap.get('net_profit', 0.0):.2f}")
    c3.metric("Loss Σ", f"₹{snap.get('net_loss', 0.0):.2f}")
    
    # Kill switch
    st.markdown("### Controls")
    ks_on = st.checkbox("Enable Kill Switch")
    tp = st.number_input("Take Profit (₹)", min_value=0.0)
    sl = st.number_input("Stop Loss (₹)", min_value=0.0)
    pnl_monitor.arm_kill_switch(ks_on, tp, sl)


# =========================================================
# SECTION 4: DEBUG PANELS (Optional, Collapsible)
# =========================================================
with st.expander("🔧 Debug Panels", expanded=False):
    st.markdown("### Linker State")
    st.json(linker.snapshot())
    
    if st.session_state.get("gtt"):
        st.markdown("### GTT Watcher State")
        st.json(st.session_state["gtt"].snapshot())
    
    if st.session_state.get("ws"):
        st.markdown("### WebSocket State")
        try:
            st.json(st.session_state["ws"].snapshot())
        except Exception as e:
            st.error(f"WS snapshot error: {e}")
