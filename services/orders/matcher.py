# services/orders/matcher.py
# Legacy SELL safety system:
# - Fetch actual sellable quantities (holdings + intraday positions)
# - Cap SELL intents so qty never exceeds availability
# - Used ONLY when user enables "auto-cap" checkbox

from typing import Dict, List, Tuple
from models import OrderIntent


# ======================================================================
# FETCH SELLABLE QUANTITIES
# ======================================================================

def fetch_sellable_quantities(kite) -> Dict[Tuple[str, str, str], int]:
    """
    Returns a dict keyed by (exchange, symbol, product)
    giving the maximum SELLABLE quantity.

    For NRML-only trading, product is effectively "NRML".
    """
    sellable = {}

    try:
        # Holdings
        holdings = kite.holdings()
        for h in holdings:
            key = (h["exchange"], h["tradingsymbol"], "NRML")
            sellable[key] = sellable.get(key, 0) + int(h.get("quantity", 0))

        # Positions (today's BFO/BF/O FNOs become NRML)
        positions = kite.positions()
        net = positions.get("net", [])
        for p in net:
            if p.get("product") != "NRML":
                continue
            qty = int(p.get("quantity", 0))
            if qty > 0:
                key = (p["exchange"], p["tradingsymbol"], "NRML")
                sellable[key] = sellable.get(key, 0) + qty

    except Exception:
        # Fail safe — don't block orders
        return {}

    return sellable


# ======================================================================
# CAP SELL INTENTS
# ======================================================================

def cap_sell_intents_by_sellable(
    intents: List[OrderIntent],
    sellable: Dict[Tuple[str, str, str], int],
    strict_product: bool = True,
):
    """
    For each SELL intent, ensures:
        SELL qty ≤ sellable qty.

    Returns:
        (new_intents, cap_report_df_data)

    Does NOT touch WS-linked SELLs.
    """
    capped = []
    report = []

    for intent in intents:
        if intent.txn_type != "SELL":
            capped.append(intent)
            continue

        # Linked SELLs should NOT be capped
        if intent.tag and intent.tag.startswith("link:"):
            capped.append(intent)
            report.append({
                "symbol": intent.symbol,
                "group": intent.tag,
                "original_qty": intent.qty,
                "capped_qty": intent.qty,
                "reason": "WS-linked → not capped",
            })
            continue

        key = (intent.exchange, intent.symbol, "NRML")
        available = sellable.get(key, 0)

        if available <= 0:
            # fully blocked
            report.append({
                "symbol": intent.symbol,
                "group": intent.tag,
                "original_qty": intent.qty,
                "capped_qty": 0,
                "reason": "No sellable qty",
            })
            continue

        new_qty = min(intent.qty, available)

        report.append({
            "symbol": intent.symbol,
            "group": intent.tag,
            "original_qty": intent.qty,
            "capped_qty": new_qty,
            "reason": "Capped to availability",
        })

        # Rebuild intent with updated qty
        capped.append(intent.copy(update={"qty": new_qty}))

    return capped, report
