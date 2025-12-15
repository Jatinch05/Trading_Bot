# services/orders/pipeline.py

from typing import List, Dict, Any
from models import OrderIntent

from services.orders.placement import place_orders
from services.orders.gtt import place_gtts
from services.ws import linker as ws_linker


def execute_bundle(
    intents: List[OrderIntent],
    kite=None,
    live: bool = True,
    link_sells_via_ws: bool = True,
) -> List[Dict[str, Any]]:

    results = []

    buys = [i for i in intents if i.txn_type == "BUY"]
    sells = [i for i in intents if i.txn_type == "SELL"]

    immediate_sells = []
    deferred_sells = []

    for s in sells:
        if link_sells_via_ws and s.tag and s.tag.startswith("link:"):
            deferred_sells.append(s)
        else:
            immediate_sells.append(s)

    regular = [i for i in buys + immediate_sells if i.gtt == "NO"]
    gtts = [i for i in buys + immediate_sells if i.gtt == "YES"]

    if regular:
        df = place_orders(regular, kite, live)
        results.extend(df.to_dict("records"))

    if live and gtts:
        df = place_gtts(gtts, kite)
        results.extend(df.to_dict("records"))

    if deferred_sells:
        ws_linker.defer_sells(deferred_sells)
        for s in deferred_sells:
            results.append({
                "kind": "DEFERRED_SELL",
                "symbol": s.symbol,
                "qty": s.qty,
                "status": "QUEUED",
            })

    return results
