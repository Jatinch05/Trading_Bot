from __future__ import annotations
import streamlit as st
import pandas as pd
from typing import List
from models import OrderIntent
from services.orders.splitter import split_regular_gtt
from services.orders.placement import place_orders
from services.orders.gtt import place_gtts_single, place_gtts_oco
from services.orders.matcher import fetch_sellable_quantities, filter_sell_intents_exact
from services.results import dataframe_to_excel_download
from services.ws import linker as ws_linker

def execute_bundle(
    intents: List[OrderIntent],
    kite,
    live: bool,
    enforce_exact_sell: bool,
    link_sells_via_ws: bool = False,
    api_key: str | None = None,
    access_token: str | None = None,
):
    """
    - If link_sells_via_ws=True (and live), SELLs are deferred to the WS linker.
      Only BUY orders (regular + GTT) are placed now.
    - Else, normal flow with optional exact-match SELL enforcement.
    """
    if live and link_sells_via_ws:
        # Split BUY vs SELL first
        buys = [i for i in intents if i.txn_type == "BUY"]
        sells = [i for i in intents if i.txn_type == "SELL"]

        # Start WS linker if needed (configure with client + placement functions)
        if not ws_linker.is_running():
            if not api_key or not access_token:
                st.error("WebSocket linker needs api_key and access_token.")
                return
            ws_linker.configure(
                kite_client=kite,
                place_regular_fn=place_orders,
                place_gtt_single_fn=place_gtts_single,
                place_gtt_oco_fn=place_gtts_oco,
            )
            ws_linker.start(api_key=api_key, access_token=access_token)
            st.success("Buy→Sell WebSocket linker started.")

        # Queue SELLs (both regular and GTT)
        queued = ws_linker.defer_sells(sells)
        st.info(f"Deferred SELL intents queued: {queued}")

        # Place only BUYs now
        regular_buys, gtt_buys = split_regular_gtt(buys)

        reg_df = pd.DataFrame()
        if regular_buys:
            with st.status("Placing BUY regular orders…", expanded=True) as s:
                reg_df = place_orders(regular_buys, kite=kite, live=True)
                s.update(state="complete")
        st.subheader("Results — BUY Regular")
        if not reg_df.empty:
            st.dataframe(reg_df, use_container_width=True)
            data, fname = dataframe_to_excel_download(reg_df)
            st.download_button("Download results_buy_regular.xlsx", data=data, file_name=fname.replace("results", "results_buy_regular"), use_container_width=True)
        else:
            st.info("No BUY regular orders to place.")

        gtt_single_df = pd.DataFrame()
        gtt_oco_df = pd.DataFrame()
        if gtt_buys:
            with st.status("Creating BUY GTTs…", expanded=True) as s2:
                gtt_single_df = place_gtts_single(gtt_buys, kite=kite)
                gtt_oco_df = place_gtts_oco(gtt_buys, kite=kite)
                s2.update(state="complete")

        st.subheader("Results — BUY GTT (Single)")
        st.dataframe(gtt_single_df, use_container_width=True) if not gtt_single_df.empty else st.info("No BUY SINGLE GTTs.")

        st.subheader("Results — BUY GTT (OCO)")
        st.dataframe(gtt_oco_df, use_container_width=True) if not gtt_oco_df.empty else st.info("No BUY OCO GTTs.")

        st.warning("SELLs are deferred and will be fired automatically when matching BUY fills accumulate via WebSocket.")
        return

    # ---- Normal path (no WS linker): optional exact-match SELL enforcement ----
    if live and enforce_exact_sell:
        pool = fetch_sellable_quantities(kite)
        intents, report = filter_sell_intents_exact(intents, pool)
        st.subheader("Sell Exact-Match Report")
        st.dataframe(report, use_container_width=True) if not report.empty else st.info("No SELL rows in this batch.")

    regular_intents, gtt_intents = split_regular_gtt(intents)

    reg_df = pd.DataFrame()
    if regular_intents:
        with st.status("Placing regular orders…", expanded=True) as s:
            reg_df = place_orders(regular_intents, kite=kite, live=live)
            s.update(state="complete")
    st.subheader("Results — Regular Orders")
    if not reg_df.empty:
        st.dataframe(reg_df, use_container_width=True)
        data, fname = dataframe_to_excel_download(reg_df)
        st.download_button("Download results_regular.xlsx", data=data, file_name=fname, use_container_width=True)
    else:
        st.info("No regular orders to place.")

    gtt_single_df = pd.DataFrame()
    gtt_oco_df    = pd.DataFrame()
    if gtt_intents and live:
        with st.status("Creating GTTs…", expanded=True) as s2:
            gtt_single_df = place_gtts_single(gtt_intents, kite=kite)
            gtt_oco_df = place_gtts_oco(gtt_intents, kite=kite)
            s2.update(state="complete")
    elif gtt_intents and not live:
        st.info("GTT creation is skipped in Dry-run.")

    st.subheader("Results — GTT (Single-leg)")
    st.dataframe(gtt_single_df, use_container_width=True) if not gtt_single_df.empty else st.info("No SINGLE GTTs to create.")
    st.subheader("Results — GTT (OCO)")
    st.dataframe(gtt_oco_df, use_container_width=True) if not gtt_oco_df.empty else st.info("No OCO GTTs to create.")
