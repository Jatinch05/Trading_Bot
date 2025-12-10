# services/placer.py — NRML-only; uses injected kite client only

from __future__ import annotations
from typing import List, Dict, Any
import pandas as pd

from models import OrderIntent


def _build_payload(it: OrderIntent) -> Dict[str, Any]:
    """
    Build a Zerodha place_order payload from OrderIntent.
    NRML-only. Variety defaults to 'regular'. Validity defaults to 'DAY'.
    """
    payload: Dict[str, Any] = {
        "exchange": it.exchange,
        "tradingsymbol": it.symbol,
        "transaction_type": it.txn_type,
        "quantity": int(it.qty),
        "product": "NRML",
        "variety": (it.variety or "regular").lower(),  # API expects lower for variety
        "validity": (it.validity or "DAY").upper(),
        "order_type": it.order_type,  # MARKET / LIMIT / SL / SL-M
        "price": None,                # set below
        "trigger_price": None,        # set below
        "disclosed_quantity": int(it.disclosed_qty or 0),
        "tag": (it.tag or "")[:20],   # Kite tag limit = 20 chars
    }

    ot = it.order_type
    if ot == "MARKET":
        payload["price"] = 0
        payload["trigger_price"] = 0
    elif ot == "LIMIT":
        payload["price"] = float(it.price)
        payload["trigger_price"] = 0
    elif ot in {"SL", "SL-M"}:
        payload["trigger_price"] = float(it.trigger_price)
        payload["price"] = float(it.price) if ot == "SL" else 0.0
    else:
        raise ValueError(f"Unsupported order_type: {ot}")

    return payload


def place_orders(intents: List[OrderIntent], kite=None, live: bool = False) -> pd.DataFrame:
    """
    Place regular (non-GTT) orders. Uses ONLY the injected `kite` when live=True.
    If live=False, simulates responses.
    Returns a DataFrame of results.
    """
    results: List[Dict[str, Any]] = []

    for idx, it in enumerate(intents):
        if (it.gtt or "").upper() == "YES":
            continue  # defensive: only regular orders here

        row: Dict[str, Any] = {
            "idx": idx,
            "symbol": it.symbol,
            "exchange": it.exchange,
            "txn_type": it.txn_type,
            "qty": int(it.qty),
            "order_type": it.order_type,
            "product": "NRML",
            "variety": (it.variety or "regular").lower(),
            "validity": (it.validity or "DAY").upper(),
            "ok": False,
            "order_id": None,
            "error": None,
        }

        try:
            payload = _build_payload(it)

            if not live:
                row.update({"ok": True, "order_id": f"SIM-{idx:05d}"})
            else:
                if kite is None:
                    raise RuntimeError("kite client is required in live mode")
                order_id = kite.place_order(**payload)
                row.update({"ok": True, "order_id": order_id})

        except Exception as e:
            row["error"] = str(e)

        results.append(row)

    return pd.DataFrame(results)
