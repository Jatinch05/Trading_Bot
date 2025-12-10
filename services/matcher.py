# services/matcher.py
from __future__ import annotations

from typing import Dict, Tuple, List, Iterable
import pandas as pd

from models import OrderIntent

Key = Tuple[str, str, str]  # (exchange, symbol, product)


def _key(exchange: str, symbol: str, product: str | None) -> Key:
    return (str(exchange).upper(), str(symbol).upper(), (product or "CNC").upper())


def _safe_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def fetch_sellable_quantities(kite) -> Dict[Key, int]:
    """
    Build a map of sellable quantity per (exchange, symbol, product).
    - CNC holdings contribute to (exchange, symbol, CNC)
    - Today's intraday/day positions contribute available = max(buys - sells, 0) on that product
    Returns: dict[(EXCHANGE, SYMBOL, PRODUCT)] -> int
    """
    sellable: Dict[Key, int] = {}

    # 1) CNC holdings
    try:
        for h in kite.holdings():
            exch = str(h.get("exchange") or h.get("exchange_type") or "NSE").upper()
            sym = str(h.get("tradingsymbol") or h.get("trading_symbol")).upper()
            qty = _safe_int(h.get("quantity") or h.get("t1_quantity") or 0)
            if qty > 0:
                k = _key(exch, sym, "CNC")
                sellable[k] = sellable.get(k, 0) + qty
    except Exception:
        # Ignore holdings failure; continue with positions
        pass

    # 2) Day positions: available = max(buys - sells, 0)
    try:
        pos = kite.positions() or {}
        day_positions = pos.get("day") or pos.get("Day") or []
        for p in day_positions:
            exch = str(p.get("exchange") or "NSE").upper()
            sym = str(p.get("tradingsymbol")).upper()
            product = str(p.get("product") or "MIS").upper()

            # Prefer explicit day buy/sell numbers when available
            buys = _safe_int(p.get("buy_quantity") or p.get("day_buy_quantity") or 0)
            sells = _safe_int(p.get("sell_quantity") or p.get("day_sell_quantity") or 0)
            avail = max(buys - sells, 0)
            if avail > 0:
                k = _key(exch, sym, product)
                sellable[k] = sellable.get(k, 0) + avail
    except Exception:
        pass

    return sellable


def cap_sell_intents_by_sellable(
    intents: Iterable[OrderIntent],
    sellable: Dict[Key, int],
    strict_product: bool = True,
) -> tuple[list[OrderIntent], pd.DataFrame]:
    """
    For each SELL intent, cap quantity to what's sellable:
      - key = (exchange, symbol, product) if strict_product else (exchange, symbol, "CNC" if product==CNC else product)
    Returns:
      - adjusted intents (SELL rows capped; zero-qty SELLs dropped)
      - report dataframe with requested/capped/available per row
    """
    adjusted: List[OrderIntent] = []
    report_rows: List[dict] = []

    for it in intents:
        if it.txn_type != "SELL":
            adjusted.append(it)
            continue

        prod = (it.product or "CNC").upper()
        k = _key(it.exchange, it.symbol, prod)

        # Optionally relax product matching: e.g., allow MIS SELL to consume CNC if prod mismatch not desired
        if not strict_product and prod != "CNC" and k not in sellable:
            k = _key(it.exchange, it.symbol, "CNC")

        available = sellable.get(k, 0)
        req = int(it.qty)
        cap = min(req, available)

        report_rows.append({
            "symbol": it.symbol,
            "exchange": it.exchange,
            "product": prod,
            "requested_qty": req,
            "available_qty": available,
            "placed_qty": cap,
            "shortfall": max(req - cap, 0),
        })

        if cap <= 0:
            # Skip placement entirely for this SELL
            continue

        # Create a capped clone
        capped = it.model_copy(update={"qty": cap})
        adjusted.append(capped)

        # Decrement pool (FIFO by intent order)
        sellable[k] = max(available - cap, 0)

    report = pd.DataFrame(report_rows)
    return adjusted, report
