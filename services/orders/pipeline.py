# services/orders/pipeline.py
"""
Pipeline:
▪ Receives a list of OrderIntent
▪ Splits BUY vs SELL
▪ BUY:
    - Live → place_order() or place_gtt()
    - Dry-run → produce synthetic order_id & register BUY in linker
▪ SELL:
    - If link_sells_via_ws=True & tag=link:<group> → deferred SELL (queued)
    - Else → immediate SELL order
"""

from __future__ import annotations
from typing import List, Dict, Any

from models import OrderIntent
from services.ws import linker as ws_linker
from services.orders.placement import place_orders


def _intent_to_linker_dict(it: OrderIntent) -> Dict[str, Any]:
    """
    Convert OrderIntent → dict format expected by ws_linker.defer_sells().
    """
    kind = "regular"
    if it.gtt == "YES" and it.gtt_type == "SINGLE":
        kind = "gtt-single"
    elif it.gtt == "YES" and it.gtt_type == "OCO":
        kind = "gtt-oco"

    return {
        "exchange": it.exchange,
        "symbol": it.symbol,
        "quantity": it.qty,
        "tag": it.tag,
        "kind": kind,
        "meta": {
            "price": it.price,
            "trigger_price": it.trigger_price,
            "gtt_trigger": it.gtt_trigger,
            "gtt_limit": it.gtt_limit,
            "gtt_trigger_1": it.gtt_trigger_1,
            "gtt_limit_1": it.gtt_limit_1,
            "gtt_trigger_2": it.gtt_trigger_2,
            "gtt_limit_2": it.gtt_limit_2,
        },
    }


def execute_bundle(
    intents: List[OrderIntent],
    kite=None,
    live: bool = True,
    link_sells_via_ws: bool = True,
) -> List[Dict[str, Any]]:
    """
    Main execution entrypoint.
    Returns list of dict results (regular, gtt, deferred sells).
    """

    results: List[Dict[str, Any]] = []

    # Split BUY vs SELL
    buys = []
    sells = []

    for it in intents:
        if it.txn_type.upper() == "BUY":
            buys.append(it)
        else:
            sells.append(it)

    # -----------------------------
    #  SELL path
    # -----------------------------
    deferred_sells = []
    immediate_sells = []

    for it in sells:
        if link_sells_via_ws and it.tag and it.tag.startswith("link:"):
            # group-linked SELL → DEFER
            deferred_sells.append(it)
        else:
            immediate_sells.append(it)

    # -----------------------------
    #  Execute BUYs + immediate SELLs
    # -----------------------------
    if buys or immediate_sells:
        combined = buys + immediate_sells
        df = place_orders(combined, kite=kite, live=live)

        # convert DataFrame to list of dict
        for _, row in df.iterrows():
            results.append(row.to_dict())

    # -----------------------------
    #  Defer group-linked SELLs
    # -----------------------------
    if deferred_sells:
        # Convert OrderIntent objects → dicts for linker
        payloads = [_intent_to_linker_dict(it) for it in deferred_sells]

        queue_ids = ws_linker.defer_sells(payloads)

        for qid in queue_ids:
            results.append({
                "kind": "DEFERRED_SELL",
                "queue_id": qid,
                "status": "QUEUED",
            })

    return results
