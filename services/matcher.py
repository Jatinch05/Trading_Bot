# services/matcher.py — SELL auto-cap helpers (NRML-only)

from __future__ import annotations
from typing import Dict, Tuple, List, Iterable
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
        # Prefer 'net' which reflects current net positions
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
        # On failure, return empty sellable; caller should handle gracefully
        return {}

    return sellable


def cap_sell_intents_by_sellable(
    intents: Iterable[OrderIntent],
    sellable: Dict[Key, int],
    strict_product: bool = True,
) -> Tuple[List[OrderIntent], pd.DataFrame]:
    """
    For SELL regular orders, cap quantity to what's available in `sellable`.
    Returns (adjusted_intents, report_df).
    If strict_product=False, we aggregate availability across all products for that (exchange,symbol).
    With NRML-only engine, both modes behave the same, but we keep the switch for future extensibility.
    """
    adjusted: List[OrderIntent] = []
    report_rows: List[Dict[str, object]] = []

    # Build relaxed index if needed
    by_ex_sym: Dict[Tuple[str, str], int] = {}
    if not strict_product:
        for (ex, sym, prod), qty in sellable.items():
            by_ex_sym[(ex, sym)] = by_ex_sym.get((ex, sym), 0) + qty

    for it in intents:
        if (it.gtt or "").upper() == "YES":
            # Only regular orders are matched/capped; GTT fires later at broker
            adjusted.append(it)
            continue

        if it.txn_type != "SELL":
            adjusted.append(it)
            continue

        key = (it.exchange, it.symbol, it.product or "NRML")
        avail = 0
        if strict_product:
            avail = int(sellable.get(key, 0))
        else:
            avail = int(by_ex_sym.get((it.exchange, it.symbol), 0))

        req = int(it.qty)
        place_qty = min(req, max(avail, 0))

        report_rows.append({
            "exchange": it.exchange,
            "symbol": it.symbol,
            "product": it.product or "NRML",
            "requested_qty": req,
            "available_qty": avail,
            "placed_qty": place_qty,
            "shortfall": max(req - place_qty, 0),
        })

        if place_qty <= 0:
            # skip placing this SELL
            continue

        adjusted.append(it.model_copy(update={"qty": place_qty}))

        # Decrement pool
        if strict_product:
            sellable[key] = max(avail - place_qty, 0)
        else:
            by_ex_sym[(it.exchange, it.symbol)] = max(avail - place_qty, 0)

    report_df = pd.DataFrame(report_rows)
    return adjusted, report_df
