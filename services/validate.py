# services/validate.py
from __future__ import annotations

from typing import Tuple, List, Dict, Any
import pandas as pd

from models import OrderIntent

# ----------------------------
# Constants & helpers
# ----------------------------
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

# ----------------------------
# Public API
# ----------------------------
def normalize_and_validate(raw_df: pd.DataFrame, instruments) -> Tuple[List[OrderIntent], pd.DataFrame, List[Tuple[int, str]]]:
    """
    Normalizes and validates the uploaded Excel rows and returns:
      - intents: List[OrderIntent]
      - vdf:     pd.DataFrame (augmented/normalized rows)
      - errors:  List[(row_index, error_message)]

    This version is robust for GTT rows:
      - If gtt == YES, we coerce order_type to 'MARKET' when blank/invalid
      - Enforce SINGLE vs OCO numeric requirements
      - Regular orders remain strict
    """
    intents: List[OrderIntent] = []
    errors: List[Tuple[int, str]] = []
    aug_rows: List[Dict[str, Any]] = []

    # Normalize column names once
    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    for i, row in df.iterrows():
        try:
            cleaned = coerce_and_validate_row_for_intent(row.to_dict())

            # If you augment with instrument metadata, do it here.
            # Example placeholder (no-op):
            # cleaned.update(resolve_instrument_fields(cleaned, instruments))

            intent = OrderIntent(**cleaned)
            intents.append(intent)
            aug_rows.append(intent.model_dump())
        except Exception as e:
            errors.append((i, str(e)))

    vdf = pd.DataFrame(aug_rows)
    return intents, vdf, errors

# ----------------------------
# Row coercion & validation
# ----------------------------
def coerce_and_validate_row_for_intent(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize pandas row dict into a clean dict for OrderIntent.
    Handles:
      - NaN/blank coercions
      - GTT-specific defaults and validation (SINGLE / OCO)
      - Regular order cross-field checks
    Returns a new dict; does not mutate the original.
    """
    r = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in row.items()}

    # Base fields (case-insensitive access)
    symbol = _get(r, "symbol", "tradingsymbol", "Symbol")
    exchange = _get(r, "exchange", "Exchange")
    txn_type = _get(r, "txn_type", "transaction_type", "TransactionType")
    qty = _get(r, "qty", "quantity", "Quantity")

    # GTT switches
    gtt = _u(_get(r, "gtt", "IsGTT", default=""))
    gtt_type = _u(_get(r, "gtt_type", "GTTType", default="")) or "SINGLE"

    # Mandatory basics
    if not symbol or not exchange:
        raise ValueError("Missing required: symbol/exchange")
    if _u(txn_type) not in {"BUY", "SELL"}:
        raise ValueError("txn_type must be BUY or SELL")
    qty = _int_or_err(qty, "qty")
    if qty < 1:
        raise ValueError("qty must be >= 1")

    # Order type handling
    order_type_raw = _get(r, "order_type", "OrderType")
    order_type_u = _u(order_type_raw)

    # GTT logic
    if gtt == "YES":
        if gtt_type not in {"SINGLE", "OCO"}:
            raise ValueError("gtt_type must be SINGLE or OCO")

        if gtt_type == "SINGLE":
            trigger_price = _get(r, "trigger_price", "TriggerPrice")
            limit_price = _get(r, "limit_price", "LimitPrice")
            _float_or_err(trigger_price, "trigger_price")
            _float_or_err(limit_price, "limit_price")

        if gtt_type == "OCO":
            tp1 = _get(r, "trigger_price_1", "TriggerPrice1")
            lp1 = _get(r, "limit_price_1", "LimitPrice1")
            tp2 = _get(r, "trigger_price_2", "TriggerPrice2")
            lp2 = _get(r, "limit_price_2", "LimitPrice2")
            tp1 = _float_or_err(tp1, "trigger_price_1")
            lp1 = _float_or_err(lp1, "limit_price_1")
            tp2 = _float_or_err(tp2, "trigger_price_2")
            lp2 = _float_or_err(lp2, "limit_price_2")
            if tp1 == tp2:
                raise ValueError("OCO trigger prices must differ")

        # For model compatibility: set a safe order_type if blank/invalid
        if order_type_u not in ALLOWED_ORDER_TYPES:
            order_type_u = "MARKET"
        # price irrelevant for GTT rows
        price = None

    else:
        # Regular orders remain strict
        if order_type_u not in ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")
        price = _get(r, "price", "Price")
        if order_type_u in {"LIMIT", "SL", "SL-M"}:
            _float_or_err(price, "price")
        else:
            price = None  # MARKET ignores price

    # Build cleaned dict for OrderIntent
    out = {
        "symbol": _s(symbol).upper(),
        "exchange": _u(exchange),
        "txn_type": _u(txn_type),
        "qty": qty,
        "order_type": order_type_u,
        "price": price,
        "trigger_price": _get(r, "trigger_price", "TriggerPrice"),
        "product": _get(r, "product", "Product") or "CNC",
        "validity": _get(r, "validity", "Validity") or "DAY",
        "variety": _get(r, "variety", "Variety") or "regular",
        "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
        "tag": _get(r, "tag", "Tag"),

        # GTT passthrough
        "gtt": "YES" if gtt == "YES" else "",
        "gtt_type": gtt_type if gtt == "YES" else "",
        "limit_price": _get(r, "limit_price", "LimitPrice"),
        "trigger_price_1": _get(r, "trigger_price_1", "TriggerPrice1"),
        "limit_price_1": _get(r, "limit_price_1", "LimitPrice1"),
        "trigger_price_2": _get(r, "trigger_price_2", "TriggerPrice2"),
        "limit_price_2": _get(r, "limit_price_2", "LimitPrice2"),
    }
    return out
