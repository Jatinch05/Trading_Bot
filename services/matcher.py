# services/matcher.py — NRML-only SELL checks

from __future__ import annotations
from typing import Dict, Tuple, List, Iterable, Any
import pandas as pd
from models import OrderIntent

Key = Tuple[str, str, str]  # (exchange, symbol, product)


def fetch_sellable_quantities(kite) -> Dict[Key, int]:
    """
    Build available SELL quantities per (exchange, symbol, product).
    NRML-only engine: we consider only NRML positions (net > 0).
    """
    sellable: Dict[Key, int] = {}
    try:
        pos = kite.positions() or {}
        for p in pos.get("net") or []:
            exch = str(p.get("exchange") or "").upper()
            sym  = str(p.get("tradingsymbol") or "").upper()
            prod = str(p.get("product") or "").upper()
            if prod != "NRML":
                continue
            netq = int(p.get("quantity") or p.get("net_quantity") or 0)
            if netq > 0:
                key = (exch, sym, prod)
                sellable[key] = sellable.get(key, 0) + netq
    except Exception:
        return {}
    return sellable


def filter_sell_intents_exact(
    intents: Iterable[OrderIntent],
    sellable: Dict[Key, int],
) -> Tuple[List[OrderIntent], pd.DataFrame]:
    """
    Enforce EXACT MATCH for all SELL intents (regular and GTT).
    - product is assumed NRML everywhere
    - if available < requested qty -> drop the SELL (do not place)
    - if available >= requested -> keep and decrement pool
    BUY intents are always kept.
    Returns (kept_intents, report_df).
    """
    kept: List[OrderIntent] = []
    rows: List[Dict[str, Any]] = []

    # work on a copy of pool
    pool = dict(sellable)

    for it in intents:
        exch = it.exchange
        sym  = it.symbol
        prod = (it.product or "NRML").upper()

        if it.txn_type != "SELL":
            kept.append(it)
            continue

        key = (exch, sym, prod)
        avail = int(pool.get(key, 0))
        req   = int(it.qty)

        if avail >= req:
            kept.append(it)
            pool[key] = avail - req
            rows.append({
                "exchange": exch, "symbol": sym, "product": prod,
                "requested_qty": req, "available_before": avail,
                "placed_qty": req, "status": "OK"
            })
        else:
            rows.append({
                "exchange": exch, "symbol": sym, "product": prod,
                "requested_qty": req, "available_before": avail,
                "placed_qty": 0, "status": "SKIPPED_INSUFFICIENT"
            })

    return kept, pd.DataFrame(rows)
