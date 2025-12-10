# services/validate.py — NRML-only

from __future__ import annotations
from typing import Tuple, List, Dict, Any
import pandas as pd
from models import OrderIntent

ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}

def _s(val) -> str:
    return "" if val is None else str(val).strip()

def _u(val) -> str:
    return _s(val).upper()

def _float_or_err(val, name: str) -> float:
    try:
        return float(val)
    except Exception:
        raise ValueError(f"{name} must be numeric")

def _int_or_err(val, name: str) -> int:
    try:
        return int(float(val))
    except Exception:
        raise ValueError(f"{name} must be an integer")

def _get(row: Dict[str, Any], *names, default=None):
    for n in names:
        if n in row:
            return row[n]
    return default

def _augment_with_instrument(cleaned: Dict[str, Any], instruments) -> Dict[str, Any]:
    # Hook for future: tick/lot/price-band checks
    return cleaned

def normalize_and_validate(raw_df: pd.DataFrame, instruments) -> Tuple[List[OrderIntent], pd.DataFrame, List[Tuple[int, str]]]:
    intents: List[OrderIntent] = []
    errors: List[Tuple[int, str]] = []
    aug_rows: List[Dict[str, Any]] = []

    if raw_df is None or raw_df.empty:
        return [], pd.DataFrame(), []

    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    for i, row in df.iterrows():
        try:
            cleaned = coerce_and_validate_row_for_intent(row.to_dict())
            cleaned = _augment_with_instrument(cleaned, instruments)
            intent = OrderIntent(**cleaned)
            intents.append(intent)
            aug_rows.append(intent.model_dump())
        except Exception as e:
            errors.append((i, str(e)))

    vdf = pd.DataFrame(aug_rows)
    return intents, vdf, errors

def coerce_and_validate_row_for_intent(row: Dict[str, Any]) -> Dict[str, Any]:
    r = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in row.items()}

    symbol   = _get(r, "symbol", "tradingsymbol", "Symbol")
    exchange = _get(r, "exchange", "Exchange")
    txn_type = _get(r, "txn_type", "transaction_type", "TransactionType")
    qty      = _get(r, "qty", "quantity", "Quantity")

    gtt      = _u(_get(r, "gtt", "IsGTT", default=""))
    gtt_type = _u(_get(r, "gtt_type", "GTTType", default="")) or "SINGLE"

    if not symbol or not exchange:
        raise ValueError("Missing required: symbol/exchange")
    if _u(txn_type) not in {"BUY", "SELL"}:
        raise ValueError("txn_type must be BUY or SELL")
    qty = _int_or_err(qty, "qty")
    if qty < 1:
        raise ValueError("qty must be >= 1")

    order_type_u = _u(_get(r, "order_type", "OrderType"))

    # ---------- GTT ----------
    if gtt == "YES":
        if gtt_type not in {"SINGLE", "OCO"}:
            raise ValueError("gtt_type must be SINGLE or OCO")

        product_u = "NRML"     # NRML-only engine
        variety_u = "regular"
        validity_u = "DAY"

        if gtt_type == "SINGLE":
            trigger_price = _float_or_err(_get(r, "trigger_price", "TriggerPrice"), "trigger_price")
            limit_price   = _float_or_err(_get(r, "limit_price",   "LimitPrice"),   "limit_price")
        else:
            tp1 = _float_or_err(_get(r, "trigger_price_1", "TriggerPrice1"), "trigger_price_1")
            lp1 = _float_or_err(_get(r, "limit_price_1",   "LimitPrice1"),   "limit_price_1")
            tp2 = _float_or_err(_get(r, "trigger_price_2", "TriggerPrice2"), "trigger_price_2")
            lp2 = _float_or_err(_get(r, "limit_price_2",   "LimitPrice2"),   "limit_price_2")
            if tp1 == tp2:
                raise ValueError("OCO trigger prices must differ")

        if order_type_u not in ALLOWED_ORDER_TYPES:
            order_type_u = "MARKET"  # irrelevant for GTT legs

        return {
            "symbol": _s(symbol).upper(),
            "exchange": _u(exchange),
            "txn_type": _u(txn_type),
            "qty": qty,
            "order_type": order_type_u,
            "price": None,
            "product": product_u,
            "validity": validity_u,
            "variety": variety_u,
            "trigger_price": trigger_price if gtt_type == "SINGLE" else None,
            "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
            "tag": _get(r, "tag", "Tag"),
            "gtt": "YES",
            "gtt_type": gtt_type,
            "limit_price":  (limit_price if gtt_type == "SINGLE" else None),
            "trigger_price_1": (tp1 if gtt_type == "OCO" else None),
            "limit_price_1":   (lp1 if gtt_type == "OCO" else None),
            "trigger_price_2": (tp2 if gtt_type == "OCO" else None),
            "limit_price_2":   (lp2 if gtt_type == "OCO" else None),
        }

    # ---------- Regular (non-GTT) ----------
    if order_type_u not in ALLOWED_ORDER_TYPES:
        raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")

    price = _get(r, "price", "Price")
    if order_type_u in {"LIMIT", "SL", "SL-M"}:
        price = _float_or_err(price, "price")
    else:
        price = None

    trig = _get(r, "trigger_price", "TriggerPrice")
    if order_type_u in {"SL", "SL-M"}:
        trig = _float_or_err(trig, "trigger_price")
    else:
        trig = None

    return {
        "symbol": _s(symbol).upper(),
        "exchange": _u(exchange),
        "txn_type": _u(txn_type),
        "qty": qty,
        "order_type": order_type_u,
        "price": price,
        "trigger_price": trig,
        "product": "NRML",                 # always NRML
        "validity": _get(r, "validity", "Validity") or "DAY",
        "variety": _get(r, "variety", "Variety") or "regular",
        "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
        "tag": _get(r, "tag", "Tag"),
        "gtt": "",
        "gtt_type": "",
        "limit_price": None,
        "trigger_price_1": None,
        "limit_price_1": None,
        "trigger_price_2": None,
        "limit_price_2": None,
    }
