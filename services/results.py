import io
import pandas as pd
import streamlit as st
from pathlib import Path

def dataframe_to_excel_download(df: pd.DataFrame):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    bio.seek(0)
    return bio, "results.xlsx"

def show_info_df(df: pd.DataFrame, msg: str):
    st.error(msg)
    st.dataframe(df, use_container_width=True)

def load_creds(path: Path) -> dict:
    try:
        if path.exists():
            import json
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}

def save_creds(path: Path, data: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(data))
    except Exception:
        pass

def ensure_live_client_or_stop(state, api_key):
    if state["kite"] is None or state["access_token"] is None:
        st.error("Live action requires a valid session token. Exchange your token first.")
        st.stop()
    if state.get("api_key_used_for_token") != api_key:
        st.error("API key changed after token exchange. Please exchange a new token.")
        st.stop()
    state["kite"].set_access_token(state["access_token"])
    try:
        prof = state["kite"].profile()
    except Exception as e:
        st.error(f"Session invalid: {e}. Please exchange a new token.")
        st.stop()
    return state["kite"], prof
