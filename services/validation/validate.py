from __future__ import annotations
from typing import Tuple, List, Dict, Any
import pandas as pd
from models import OrderIntent

ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}

def _s(v): return "" if v is None else str(v).strip()
def _u(v): return _s(v).upper()
def _float(v, name): 
    try: return float(v)
    except: raise ValueError(f"{name} must be numeric")
def _int(v, name):
    try: return int(float(v))
    except: raise ValueError(f"{name} must be int")
def _get(row: Dict[str, Any], *names, default=None):
    for n in names:
        if n in row: return row[n]
    return default

def normalize_and_validate(raw_df: pd.DataFrame, instruments) -> Tuple[List[OrderIntent], pd.DataFrame, List[Tuple[int,str]]]:
    intents, errors, aug = [], [], []
    if raw_df is None or raw_df.empty:
        return [], pd.DataFrame(), []
    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    for i, row in df.iterrows():
        try:
            cleaned = coerce_row(row.to_dict())
            oi = OrderIntent(**cleaned)
            intents.append(oi)
            aug.append(oi.model_dump())
        except Exception as e:
            errors.append((i, str(e)))
    return intents, pd.DataFrame(aug), errors

def coerce_row(r: Dict[str, Any]) -> Dict[str, Any]:
    r = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in r.items()}

    symbol   = _get(r, "symbol", "tradingsymbol", "Symbol")
    exchange = _get(r, "exchange", "Exchange")
    txn_type = _get(r, "txn_type", "transaction_type", "TransactionType")
    qty      = _get(r, "qty", "quantity", "Quantity")
    order_type = _u(_get(r, "order_type", "OrderType"))

    if not symbol or not exchange: raise ValueError("symbol/exchange required")
    txn_type_u = _u(txn_type)
    if txn_type_u not in {"BUY", "SELL"}: raise ValueError("txn_type must be BUY/SELL")
    qty = _int(qty, "qty")
    if qty < 1: raise ValueError("qty >= 1")

    gtt = _u(_get(r, "gtt", "IsGTT", default=""))
    gtt_type = _u(_get(r, "gtt_type", "GTTType", default="")) or "SINGLE"

    if gtt == "YES":
        if gtt_type == "SINGLE":
            tp = _float(_get(r, "trigger_price", "TriggerPrice"), "trigger_price")
            lp = _float(_get(r, "limit_price", "LimitPrice"), "limit_price")
            return {
                "symbol": _u(symbol), "exchange": _u(exchange), "txn_type": txn_type_u, "qty": qty,
                "order_type": order_type if order_type in ALLOWED_ORDER_TYPES else "MARKET",
                "price": None, "trigger_price": tp,
                "product": "NRML", "validity": "DAY", "variety": "regular",
                "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
                "tag": _get(r, "tag", "Tag"), "gtt": "YES", "gtt_type": "SINGLE",
                "limit_price": lp,
            }
        elif gtt_type == "OCO":
            tp1 = _float(_get(r, "trigger_price_1", "TriggerPrice1"), "trigger_price_1")
            lp1 = _float(_get(r, "limit_price_1", "LimitPrice1"), "limit_price_1")
            tp2 = _float(_get(r, "trigger_price_2", "TriggerPrice2"), "trigger_price_2")
            lp2 = _float(_get(r, "limit_price_2", "LimitPrice2"), "limit_price_2")
            if tp1 == tp2: raise ValueError("OCO triggers must differ")
            return {
                "symbol": _u(symbol), "exchange": _u(exchange), "txn_type": txn_type_u, "qty": qty,
                "order_type": order_type if order_type in ALLOWED_ORDER_TYPES else "MARKET",
                "price": None, "trigger_price": None,
                "product": "NRML", "validity": "DAY", "variety": "regular",
                "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
                "tag": _get(r, "tag", "Tag"), "gtt": "YES", "gtt_type": "OCO",
                "trigger_price_1": tp1, "limit_price_1": lp1,
                "trigger_price_2": tp2, "limit_price_2": lp2,
            }
        else:
            raise ValueError("gtt_type must be SINGLE/OCO")

    # regular
    if order_type not in ALLOWED_ORDER_TYPES:
        raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")
    price = _get(r, "price", "Price")
    trig  = _get(r, "trigger_price", "TriggerPrice")
    if order_type in {"LIMIT", "SL", "SL-M"}: price = _float(price, "price")
    else: price = None
    if order_type in {"SL", "SL-M"}: trig = _float(trig, "trigger_price")
    else: trig = None

    return {
        "symbol": _u(symbol), "exchange": _u(exchange), "txn_type": txn_type_u, "qty": qty,
        "order_type": order_type, "price": price, "trigger_price": trig,
        "product": "NRML", "validity": _get(r, "validity", "Validity") or "DAY",
        "variety": _get(r, "variety", "Variety") or "regular",
        "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
        "tag": _get(r, "tag", "Tag"),
        "gtt": "", "gtt_type": "",
    }
