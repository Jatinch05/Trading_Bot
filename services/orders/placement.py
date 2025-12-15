# services/orders/placement.py

import pandas as pd
from uuid import uuid4
from models import OrderIntent
from services.ws import linker as ws_linker


def place_orders(intents, kite=None, live=True):
    rows = []

    for it in intents:
        txn = it.txn_type.upper()

        if not live:
            oid = f"DRY-{uuid4().hex[:8]}"
            if txn == "BUY":
                ws_linker.register_buy_order(oid, it.exchange, it.symbol, it.tag)
            rows.append({
                "order_id": oid,
                "symbol": it.symbol,
                "txn_type": txn,
                "qty": it.qty,
                "status": "DRYRUN",
            })
            continue

        payload = {
            "exchange": it.exchange,
            "tradingsymbol": it.symbol,
            "transaction_type": txn,
            "quantity": it.qty,
            "product": "NRML",
            "order_type": it.order_type,
            "price": it.price or 0,
            "trigger_price": it.trigger_price or 0,
            "validity": it.validity,
            "variety": it.variety,
        }

        oid = kite.place_order(**payload)

        if txn == "BUY":
            ws_linker.register_buy_order(oid, it.exchange, it.symbol, it.tag)

        rows.append({
            "order_id": oid,
            "symbol": it.symbol,
            "txn_type": txn,
            "qty": it.qty,
            "status": "OK",
        })

    return pd.DataFrame(rows)
