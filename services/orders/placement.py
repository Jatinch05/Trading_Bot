from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd
from models import OrderIntent
from services.ws.linker import register_buy_order

def _payload(it: OrderIntent) -> Dict[str, Any]:
    p = {
        "exchange": it.exchange,
        "tradingsymbol": it.symbol,
        "transaction_type": it.txn_type,
        "quantity": int(it.qty),
        "product": "NRML",
        "variety": (it.variety or "regular").lower(),
        "validity": (it.validity or "DAY").upper(),
        "order_type": it.order_type,
        "price": 0, "trigger_price": 0,
        "disclosed_quantity": int(it.disclosed_qty or 0),
        "tag": (it.tag or "")[:20],
    }
    if it.order_type == "LIMIT":
        p["price"] = float(it.price)
    elif it.order_type in {"SL", "SL-M"}:
        p["trigger_price"] = float(it.trigger_price)
        p["price"] = float(it.price) if it.order_type == "SL" else 0.0
    return p

def place_orders(intents: List[OrderIntent], kite=None, live: bool=False) -> pd.DataFrame:
    out = []
    for idx, it in enumerate(intents):
        if (it.gtt or "").upper() == "YES":  # defensive; GTTs are handled elsewhere
            continue
        row = {"idx": idx, "symbol": it.symbol, "exchange": it.exchange,
               "txn_type": it.txn_type, "qty": int(it.qty), "order_type": it.order_type,
               "product": "NRML", "ok": False, "order_id": None, "error": None}
        try:
            if not live:
                row.update(ok=True, order_id=f"SIM-{idx:05d}")
            else:
                if kite is None:
                    raise RuntimeError("kite required in live mode")
                oid = kite.place_order(**_payload(it))
                row.update(ok=True, order_id=oid)
                # Register BUY for tag-scoped WS accounting
                if it.txn_type == "BUY":
                    register_buy_order(
                        order_id=str(oid),
                        exchange=it.exchange,
                        symbol=it.symbol,
                        tag=(it.tag or None),
                    )
        except Exception as e:
            row["error"] = str(e)
        out.append(row)
    return pd.DataFrame(out)
