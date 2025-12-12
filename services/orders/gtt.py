# services/orders/gtt.py — robust SINGLE + OCO GTT creation with full NRML & WS-linking

from typing import List, Dict, Any
import pandas as pd

try:
    from kiteconnect import KiteConnect
except Exception:
    KiteConnect = None

from models import OrderIntent
from services.ws import linker as ws_linker
from services.ws import gtt_watcher


# ------------------------------------------------------
# Helpers
# ------------------------------------------------------

def _normalize_group(tag: str | None) -> str | None:
    """Extract clean group from 'link:n' tags."""
    if not tag:
        return None
    t = str(tag).strip().lower()
    if t.startswith("link:"):
        return t.split(":", 1)[1].strip()
    return None


def _build_ltp_map(kite, intents: List[OrderIntent]) -> Dict[str, float]:
    keys = sorted({f"{i.exchange}:{i.symbol}" for i in intents})
    if not keys:
        return {}
    try:
        data = kite.ltp(keys)
        out = {}
        for k in keys:
            lp = (data.get(k) or {}).get("last_price")
            out[k] = float(lp) if lp is not None else None
        return out
    except Exception:
        # fallback: unknown LTP (try individual fetch later)
        return {k: None for k in keys}


# ------------------------------------------------------
# GTT SINGLE
# ------------------------------------------------------

def place_gtts_single(intents: List[OrderIntent], kite) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    singles = [
        i for i in intents
        if str(getattr(i, "gtt", "")).upper() == "YES"
        and str(getattr(i, "gtt_type", "")).upper() == "SINGLE"
    ]
    if not singles:
        return pd.DataFrame(rows)

    ltp_map = _build_ltp_map(kite, singles)

    for i in singles:
        key = f"{i.exchange}:{i.symbol}"
        ltp = ltp_map.get(key)
        status, msg, trig_id = "OK", "", None

        # Validate required fields
        if getattr(i, "gtt_trigger", None) is None or getattr(i, "gtt_limit", None) is None:
            rows.append({
                "kind": "GTT_SINGLE",
                "exchange": i.exchange,
                "symbol": i.symbol,
                "side": str(i.txn_type).upper(),
                "qty": int(i.qty),
                "trigger": None,
                "limit": None,
                "trigger_id": None,
                "status": "ERROR",
                "message": "Missing GTT trigger/limit",
            })
            continue

        try:
            # Ensure we have LTP
            if ltp is None:
                data = kite.ltp([key])
                ltp = float(data[key]["last_price"])

            # GTT SINGLE order child
            orders = [{
                "transaction_type": str(i.txn_type).upper(),
                "quantity": int(i.qty),
                "order_type": "LIMIT",
                "price": float(i.gtt_limit),
                "product": "NRML",
            }]

            resp = kite.place_gtt(
                trigger_type=getattr(KiteConnect, "GTT_TYPE_SINGLE", "single"),
                tradingsymbol=i.symbol,
                exchange=i.exchange,
                trigger_values=[float(i.gtt_trigger)],
                last_price=float(ltp),
                orders=orders,
            )

            trig_id = resp.get("trigger_id")

            # WS-based linking for BUY GTT
            if str(i.txn_type).upper() == "BUY":
                group = _normalize_group(getattr(i, "tag", None))
                if group and trig_id:
                    ws_linker.register_gtt_trigger(trig_id, i.exchange, i.symbol, f"link:{group}")
                    gtt_watcher.add_trigger(trig_id)

        except Exception as e:
            status, msg = "ERROR", str(e)

        rows.append({
            "kind": "GTT_SINGLE",
            "exchange": i.exchange,
            "symbol": i.symbol,
            "side": str(i.txn_type).upper(),
            "qty": int(i.qty),
            "trigger": float(i.gtt_trigger),
            "limit": float(i.gtt_limit),
            "trigger_id": trig_id,
            "status": status,
            "message": msg,
        })

    return pd.DataFrame(rows)


# ------------------------------------------------------
# GTT OCO
# ------------------------------------------------------

def place_gtts_oco(intents: List[OrderIntent], kite) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    ocos = [
        i for i in intents
        if str(getattr(i, "gtt", "")).upper() == "YES"
        and str(getattr(i, "gtt_type", "")).upper() == "OCO"
    ]
    if not ocos:
        return pd.DataFrame(rows)

    ltp_map = _build_ltp_map(kite, ocos)

    for i in ocos:
        key = f"{i.exchange}:{i.symbol}"
        ltp = ltp_map.get(key)
        status, msg, trig_id = "OK", "", None

        # Validate necessary fields
        if (
            getattr(i, "gtt_trigger_1", None) is None
            or getattr(i, "gtt_trigger_2", None) is None
            or getattr(i, "gtt_limit_1", None) is None
            or getattr(i, "gtt_limit_2", None) is None
        ):
            rows.append({
                "kind": "GTT_OCO",
                "exchange": i.exchange,
                "symbol": i.symbol,
                "side": str(i.txn_type).upper(),
                "qty": int(i.qty),
                "trigger_1": None,
                "limit_1": None,
                "trigger_2": None,
                "limit_2": None,
                "trigger_id": None,
                "status": "ERROR",
                "message": "Missing OCO triggers/limits",
            })
            continue

        try:
            # Ensure LTP
            if ltp is None:
                data = kite.ltp([key])
                ltp = float(data[key]["last_price"])

            # Two child orders
            orders = [
                {
                    "transaction_type": str(i.txn_type).upper(),
                    "quantity": int(i.qty),
                    "order_type": "LIMIT",
                    "price": float(i.gtt_limit_1),
                    "product": "NRML",
                },
                {
                    "transaction_type": str(i.txn_type).upper(),
                    "quantity": int(i.qty),
                    "order_type": "LIMIT",
                    "price": float(i.gtt_limit_2),
                    "product": "NRML",
                },
            ]

            resp = kite.place_gtt(
                trigger_type=getattr(KiteConnect, "GTT_TYPE_OCO", "oco"),
                tradingsymbol=i.symbol,
                exchange=i.exchange,
                trigger_values=[float(i.gtt_trigger_1), float(i.gtt_trigger_2)],
                last_price=float(ltp),
                orders=orders,
            )

            trig_id = resp.get("trigger_id")

            # BUY-side GTT linking
            if str(i.txn_type).upper() == "BUY":
                group = _normalize_group(getattr(i, "tag", None))
                if group and trig_id:
                    ws_linker.register_gtt_trigger(trig_id, i.exchange, i.symbol, f"link:{group}")
                    gtt_watcher.add_trigger(trig_id)

        except Exception as e:
            status, msg = "ERROR", str(e)

        rows.append({
            "kind": "GTT_OCO",
            "exchange": i.exchange,
            "symbol": i.symbol,
            "side": str(i.txn_type).upper(),
            "qty": int(i.qty),
            "trigger_1": float(i.gtt_trigger_1),
            "limit_1": float(i.gtt_limit_1),
            "trigger_2": float(i.gtt_trigger_2),
            "limit_2": float(i.gtt_limit_2),
            "trigger_id": trig_id,
            "status": status,
            "message": msg,
        })

    return pd.DataFrame(rows)
