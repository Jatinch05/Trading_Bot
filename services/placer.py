from typing import List, Dict, Any
import time
import pandas as pd
from config import PLACE_SLEEP_SEC
from models import OrderIntent

def _row(status: str, oi: OrderIntent, msg: str = "", order_id: str | None = None) -> Dict[str, Any]:
    return {
        "tradingsymbol": oi.tradingsymbol,
        "order_id": order_id,
        "status": status,                 # DRY-RUN / PLACED / ERROR
        "average_price": None,
        "filled_qty": 0,
        "exchange_timestamp": None,
        "message": msg,
    }

def _payload(oi: OrderIntent) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "variety": oi.variety,
        "exchange": oi.exchange,
        "tradingsymbol": oi.tradingsymbol,
        "transaction_type": oi.txn_type,
        "quantity": int(oi.qty),
        "product": oi.product,
        "order_type": oi.order_type,
        "validity": oi.validity or "DAY",
        "disclosed_quantity": int(oi.disclosed_qty or 0),
        "tag": oi.tag or "",
    }
    if oi.order_type in {"LIMIT", "SL"} and oi.price is not None:
        p["price"] = float(oi.price)
    if oi.order_type in {"SL", "SL-M"} and oi.trigger_price is not None:
        p["trigger_price"] = float(oi.trigger_price)
    return p

def place_orders(intents: List[OrderIntent], kite=None, live: bool = False) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []

    if not live:
        for oi in intents:
            rows.append(_row("DRY-RUN", oi, "No live orders were sent."))
        return pd.DataFrame(rows)

    if kite is None:
        for oi in intents:
            rows.append(_row("ERROR", oi, "No authenticated Kite client provided."))
        return pd.DataFrame(rows)

    # quick session probe
    try:
        _ = kite.profile()
    except Exception as e:
        msg = f"Session invalid at place_orders: {e}"
        for oi in intents:
            rows.append(_row("ERROR", oi, msg))
        return pd.DataFrame(rows)

    for idx, oi in enumerate(intents, start=1):
        try:
            payload = _payload(oi)
            # surface payload for debugging in Streamlit logs
            print(f"[{idx}/{len(intents)}] placing:", payload)
            order_id = kite.place_order(**payload)   # relies on client timeout
            rows.append(_row("PLACED", oi, order_id=order_id))
            time.sleep(PLACE_SLEEP_SEC)
        except Exception as e:
            rows.append(_row("ERROR", oi, str(e)))

    return pd.DataFrame(rows)
