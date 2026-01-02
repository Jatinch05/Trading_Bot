# app.py - Clean, production-ready Streamlit app
# Architecture: Auth → Upload → Validate → Execute → Monitor → Debug

import streamlit as st
import os
import sys
import json
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
from services.ws.runtime import ensure_workers, stop_workers
from services import pnl_monitor


# =========================================================
# PAGE SETUP
# =========================================================
"""
Ensure imports work both locally and in managed environments where the
working directory may differ. Add the project root to sys.path so that
`services.*` and `models` resolve reliably.
"""
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = APP_DIR  # repo root is same directory in this workspace
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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
    "token_exchanged_at": None,  # Timestamp when token was last exchanged
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
        # Restore state from previous session
        st.session_state["linker"].load_state()
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


def _install_release_callback(linker: OrderLinker, client):
    """Install a thread-safe callback (no Streamlit/session access in threads)."""
    from services.orders.pipeline import execute_released_sells

    def _place_released(sells: list):
        try:
            print(f"[APP] Placing {len(sells)} released SELL(s) (requested)")
            results = execute_released_sells(kite=client, sells=sells, live=True)
            print(f"[APP] ✅ Placed {len(results or [])} released SELL(s) (after idempotency)")
        except Exception as e:
            print(f"[APP] ❌ Released SELL placement failed: {e}")

    linker.set_release_callback(_place_released)


# =========================================================
# ERROR HANDLING HELPERS (UI)
# =========================================================
def _friendly_kite_error(e: Exception) -> tuple[str, str]:
    """Return (title, details) suitable for end users.

    We avoid exposing raw stack traces and instead show the actionable reason.
    """
    msg = str(e) or e.__class__.__name__
    mlow = msg.lower()

    # Common patterns
    if "incorrect `api_key` or `access_token`" in mlow:
        return (
            "Session expired or invalid",
            "Your Kite session looks invalid. Please Exchange Token again from the sidebar.",
        )
    if "trigger cannot be created with the first trigger price more than the last price" in mlow:
        return (
            "Invalid GTT triggers",
            "First trigger must be below current last price (for SELL) or above (for BUY). Adjust trigger values or wait for price to move.",
        )
    if "invalid" in mlow and "trigger" in mlow:
        return (
            "Invalid GTT parameters",
            "Please review trigger/limit values and tick sizes. Ensure order_type, product, and qty are valid for the instrument.",
        )
    if "trigger already met" in mlow:
        return (
            "Trigger already met",
            "The trigger you provided has already been crossed. For stop-style entries (including GTT single), set the trigger on the un-crossed side of the current price; for passive entries use LIMIT/MARKET instead.",
        )
    if "price" in mlow and "tick" in mlow:
        return (
            "Invalid price step",
            "Price doesn’t match the instrument’s tick size. Adjust to a valid tick increment.",
        )
    if "quantity" in mlow and ("multiple" in mlow or "lot" in mlow):
        return (
            "Invalid quantity",
            "Quantity must be a valid lot size multiple for this instrument.",
        )
    # Fallback
    return ("Zerodha rejected the request", msg)


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
        # Reset token expiry timer
        import time
        st.session_state["token_exchanged_at"] = time.time()
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
    # Also stop process-wide background workers to avoid duplicate WS/GTT threads
    stop_workers()
    st.sidebar.success("Session cleared")

# Maintenance
st.sidebar.markdown("---")
st.sidebar.markdown("## Maintenance")
st.sidebar.caption(
    "Use these only if something got out of sync after a refresh/restart. "
    "They do not cancel or modify any orders at Zerodha; they only reset this app’s local linking memory."
)

confirm_reset = st.sidebar.checkbox(
    "I understand: this will forget any pending linked SELLs",
    value=False,
)
if st.sidebar.button(
    "Reset Linked-Order Memory",
    disabled=not confirm_reset,
    use_container_width=True,
    help=(
        "Clears the app’s saved linkage between BUY fills and queued SELLs. "
        "Use when you want to start fresh or if SELLs are not releasing due to stale local state. "
        "Do NOT use while you still expect this app to auto-place pending linked SELLs for an already-placed BUY."
    ),
):
    try:
        msg = ensure_linker().reset_state()
        st.sidebar.warning(
            "Reset complete.\n\n"
            "What this does: clears local queued linked SELLs + BUY→group mappings.\n"
            "What this does NOT do: cancel any existing Kite orders/GTTs." 
        )
        st.sidebar.info(msg)
    except Exception as e:
        st.sidebar.error(f"Reset failed: {e}")

# (Sidebar Debug removed per user request)


# =========================================================
# SECTION 2: ORDER EXECUTION (Main)
# =========================================================
if st.session_state["kite"] is None:
    st.warning("⚠️ Please authenticate first (see sidebar)")
    st.stop()

client = st.session_state["kite"]
linker = ensure_linker()

# Install (or refresh) release callback with current client
_install_release_callback(linker, client)

# Check token age and warn if approaching expiry
import time
if st.session_state["token_exchanged_at"] is not None:
    token_age_hours = (time.time() - st.session_state["token_exchanged_at"]) / 3600
    if token_age_hours > 23.5:
        st.warning(
            f"⚠️ **Token is {token_age_hours:.1f} hours old** (>24h expiry). "
            "Consider exchanging a new one from the sidebar to prevent disruptions."
        )

# Ensure runtime services are initialized in LIVE mode
if live_mode:
    workers = ensure_workers(
        kite=client,
        api_key=st.session_state.get("api_key"),
        access_token=st.session_state.get("access_token"),
        linker=linker,
        token_exchanged_at=st.session_state.get("token_exchanged_at"),
    )
    # Keep references for debug panels
    st.session_state["gtt"] = workers.get("gtt")
    st.session_state["ws"] = workers.get("ws")
    st.session_state["buy_monitor"] = workers.get("buy_monitor")

st.markdown("## Order Execution")

# Excel upload
with st.expander("📋 Expected columns", expanded=False):
    st.code(
        """symbol, exchange, txn_type, qty, order_type, price, trigger_price,
product, validity, variety, disclosed_qty, tag,
gtt, gtt_type, gtt_trigger, gtt_limit, gtt_trigger_1, gtt_limit_1, gtt_trigger_2, gtt_limit_2, tolerance"""
    )
    st.caption("Use tag=link:<group> to link BUY/SELL automation. Example: tag=link:1")
    st.caption("Set tolerance (e.g., 2.0) to queue BUY orders and place them when price hits trigger_price ± tolerance")

file = st.file_uploader("Upload Excel", type=["xlsx"])
raw_df = None

if file:
    raw_df = pd.read_excel(file)
    st.dataframe(raw_df.head(20), width="stretch")
    # Reset selection state when a new file is uploaded
    if st.session_state.get("_last_file_name") != getattr(file, "name", None):
        st.session_state["_last_file_name"] = getattr(file, "name", None)
        st.session_state["validated_rows"] = []
        st.session_state["vdf_disp"] = None
        st.session_state["selected_rows"] = set()
    
    # Validate
    if st.button("✓ Validate Rows"):
        try:
            # Validate with minimal instruments loader (returns empty set if file missing)
            intents, vdf, errors = normalize_and_validate(raw_df, instruments=Instruments.load())
            st.session_state["validated_rows"] = intents
            
            if not errors:
                st.success(f"✅ All {len(intents)} rows valid")
                # Only set display df if not already edited, to preserve selections across reruns
                if st.session_state.get("vdf_disp") is None:
                    st.session_state["vdf_disp"] = vdf.copy()
                else:
                    # Update existing df values but preserve the select column
                    prev = st.session_state["vdf_disp"].copy()
                    has_select = "select" in prev.columns
                    merged = vdf.copy()
                    if has_select:
                        merged.insert(0, "select", prev.get("select", False))
                    st.session_state["vdf_disp"] = merged
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
    
    # Provide a stable key and disable autorefresh hint
    edited = st.data_editor(
        disp,
        hide_index=False,
        width="stretch",
        column_config={"select": st.column_config.CheckboxColumn("Select")},
        key="order_editor",
    )
    
    st.session_state["vdf_disp"] = edited.copy()
    # Ensure select column is boolean and handle NaN
    select_mask = edited["select"].fillna(False).astype(bool)
    st.session_state["selected_rows"] = set(edited[select_mask].index)

col_exec_all, col_exec_sel, col_clear_sel = st.columns([1,1,1])

# Execute All
exec_all_clicked = col_exec_all.button("🚀 Execute All", disabled=(len(st.session_state.get("validated_rows", [])) == 0))

# Execute Selected
exec_sel_clicked = col_exec_sel.button("🚀 Execute Selected", disabled=(len(st.session_state.get("selected_rows", set())) == 0))

# Clear Selection to prevent accidental rerun loss
clear_sel_clicked = col_clear_sel.button("🧹 Clear Selection")

if clear_sel_clicked:
    if st.session_state.get("vdf_disp") is not None:
        vdf_tmp = st.session_state["vdf_disp"].copy()
        if "select" in vdf_tmp.columns:
            vdf_tmp["select"] = False  # explicitly False (not NaN)
        st.session_state["vdf_disp"] = vdf_tmp
    st.session_state["selected_rows"] = set()

def _execute_intents(run_intents: list[OrderIntent]):
    results_rows = []
    if not live_mode:
        for i in run_intents:
            results_rows.append({
                "row": getattr(i, "source_row", None),
                "symbol": i.symbol,
                "txn_type": i.txn_type,
                "qty": i.qty,
                "order_type": getattr(i, "order_type", None),
                "trigger_price": getattr(i, "trigger_price", None),
                "price": getattr(i, "price", None),
                "status": "DRY-RUN",
            })
    else:
        for i in run_intents:
            try:
                res = execute_bundle(kite=client, intents=[i], linker=linker, live=True) or []
                for r in res:
                    r["row"] = getattr(i, "source_row", None)
                    r["order_type"] = getattr(i, "order_type", None)
                    r["trigger_price"] = getattr(i, "trigger_price", None)
                    r["price"] = getattr(i, "price", None)
                    r["api"] = "place_gtt" if getattr(i, "gtt", "NO") == "YES" else "place_order"
                    r["gtt"] = getattr(i, "gtt", None)
                    r["gtt_type"] = getattr(i, "gtt_type", None)
                    results_rows.append(r)
            except Exception as e:
                title, details = _friendly_kite_error(e)
                results_rows.append({
                    "row": getattr(i, "source_row", None),
                    "symbol": i.symbol,
                    "txn_type": i.txn_type,
                    "qty": i.qty,
                    "order_type": getattr(i, "order_type", None),
                    "trigger_price": getattr(i, "trigger_price", None),
                    "price": getattr(i, "price", None),
                    "status": "ERROR",
                    "message": f"{title}: {details}",
                    "api": "place_gtt" if getattr(i, "gtt", "NO") == "YES" else "place_order",
                    "gtt": getattr(i, "gtt", None),
                    "gtt_type": getattr(i, "gtt_type", None),
                    "raw_error": str(e),
                })
    st.subheader("Execution Results")
    st.dataframe(pd.DataFrame(results_rows), width="stretch")

if exec_all_clicked and st.session_state.get("validated_rows"):
    base_intents = st.session_state["validated_rows"]
    intents = []
    for idx, intent in enumerate(base_intents):
        data = intent.__dict__.copy()
        data["source_row"] = idx
        intents.append(OrderIntent(**data))
    _execute_intents(intents)

if exec_sel_clicked and st.session_state.get("selected_rows"):
    rows_data = []
    for i in st.session_state["selected_rows"]:
        d = st.session_state["validated_rows"][i].__dict__.copy()
        d["source_row"] = int(i)
        rows_data.append(d)
    intents = [OrderIntent(**r) for r in rows_data]
    _execute_intents(intents)


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
        title, details = _friendly_kite_error(e)
        st.error(f"{title}: {details}")
    
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
    
    # Debug: Test save/load
    st.markdown("### State File Debug")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔍 Force Save State"):
            result = linker.save_state()
            if result is None:
                st.info("Save completed (no message)")
            elif "✅" in result:
                st.success(result)
            else:
                st.error(result)
    with col2:
        if st.button("🔄 Force Load State"):
            linker.load_state()
            st.info("Load triggered! Check linker state above.")
    with col3:
        st.caption("State reset moved to sidebar → Maintenance")
    
    # Show file system info
    import os
    from pathlib import Path
    st.markdown("#### File System Info")
    st.text(f"CWD: {os.getcwd()}")
    files_in_cwd = os.listdir('.')
    st.text(f"Files in CWD ({len(files_in_cwd)}): {files_in_cwd[:15]}")
    
    # Check for state file
    state_file = Path(getattr(linker, "STATE_FILE", "linker_state.json"))
    if state_file.exists():
        st.success(f"✅ State file found: {state_file}")
        with open(state_file) as f:
            st.json(json.load(f))
    else:
        st.warning(f"❌ State file not found: {state_file}")
    
    if st.session_state.get("gtt"):
        st.markdown("### GTT Watcher State")
        st.json(st.session_state["gtt"].snapshot())
    
    if st.session_state.get("buy_monitor"):
        st.markdown("### BUY Monitor State")
        try:
            st.json(st.session_state["buy_monitor"].snapshot())
            # Show queued BUYs detail
            with linker._lock:
                if linker.buy_queue:
                    st.markdown("#### Queued BUYs")
                    buy_queue_data = []
                    for entry in linker.buy_queue:
                        intent = entry["intent"]
                        buy_queue_data.append({
                            "symbol": intent.symbol,
                            "qty": intent.qty,
                            "trigger": entry["trigger"],
                            "tolerance": entry["tolerance"],
                            "queued_at": entry.get("queued_at"),
                        })
                    st.dataframe(pd.DataFrame(buy_queue_data), width="stretch")
        except Exception as e:
            st.error(f"BUY monitor snapshot error: {e}")
    
    if st.session_state.get("ws"):
        st.markdown("### WebSocket State")
        try:
            st.json(st.session_state["ws"].snapshot())
        except Exception as e:
            st.error(f"WS snapshot error: {e}")
