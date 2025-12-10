# services/validate.py
from __future__ import annotations

from typing import Tuple, List, Dict, Any
import pandas as pd

from models import OrderIntent

# ----------------------------------
# Constants & small utilities
# ----------------------------------
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

# Optional hook point if you want to enrich with instrument data.
# Implement and call it where indicated below.
def _augment_with_instrument(cleaned: Dict[str, Any], instruments) -> Dict[str, Any]:
    """
    Placeholder for instrument augmentation. Keep no-op by default.
    You can resolve last_price, tick_size, lot_size, etc. if your Instruments service supports it.
    """
    return cleaned


# ----------------------------------
# Public API
# ----------------------------------
def normalize_and_validate(raw_df: pd.DataFrame, instruments) -> Tuple[List[OrderIntent], pd.DataFrame, List[Tuple[int, str]]]:
    """
    Normalizes and validates the uploaded Excel rows and returns:
      - intents: List[OrderIntent]
      - vdf:     pd.DataFrame (augmented/normalized rows)
      - errors:  List[(row_index, error_message)]

    Robust for mixed sheets containing both regular orders and GTT (SINGLE/OCO):
      - Non-GTT rows: strict regular order rules; GTT-only fields are nulled/ignored.
      - GTT rows: enforce CNC/regular/DAY; validate SINGLE/OCO numeric requirements;
                  default/force order_type where necessary for model compatibility.
    """
    intents: List[OrderIntent] = []
    errors: List[Tuple[int, str]] = []
    aug_rows: List[Dict[str, Any]] = []

    if raw_df is None or raw_df.empty:
        return [], pd.DataFrame(), []

    # Normalize headers once
    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    for i, row in df.iterrows():
        try:
            cleaned = coerce_and_validate_row_for_intent(row.to_dict())

            # Optional instruments augmentation
            cleaned = _augment_with_instrument(cleaned, instruments)

            intent = OrderIntent(**cleaned)
            intents.append(intent)
            aug_rows.append(intent.model_dump())
        except Exception as e:
            errors.append((i, str(e)))

    vdf = pd.DataFrame(aug_rows)
    return intents, vdf, errors


# ----------------------------------
# Core normalization for one row
# ----------------------------------
def coerce_and_validate_row_for_intent(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize pandas row dict into a clean dict for OrderIntent.

    Handles:
      - NaN → None coercion
      - Mixed-sheet logic:
          * Regular orders: strict rules; ignore GTT-only fields.
          * GTT (SINGLE/OCO): enforce constraints; coerce order_type if blank/invalid;
                              force product/variety/validity to supported values (CNC/regular/DAY).
      - Prevents leakage of GTT fields into regular orders.
    """
    # Drop pandas NaN → None
    r = {k: (None if (isinstance(v, float) and pd.isna(v)) else v) for k, v in row.items()}

    # Base fields (case-insensitive access)
    symbol   = _get(r, "symbol", "tradingsymbol", "Symbol")
    exchange = _get(r, "exchange", "Exchange")
    txn_type = _get(r, "txn_type", "transaction_type", "TransactionType")
    qty      = _get(r, "qty", "quantity", "Quantity")

    # GTT switches
    gtt      = _u(_get(r, "gtt", "IsGTT", default=""))
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

    # -------------------------
    # GTT branch
    # -------------------------
    if gtt == "YES":
        if gtt_type not in {"SINGLE", "OCO"}:
            raise ValueError("gtt_type must be SINGLE or OCO")

        # Enforce Zerodha GTT constraints for equities
        product  = _get(r, "product", "Product")
        variety  = _get(r, "variety", "Variety")
        validity = _get(r, "validity", "Validity")

        if product and _u(product) not in {"CNC", ""}:
            raise ValueError(f"GTT supports only product=CNC for equities; got {product!r}")
        if variety and _u(variety) not in {"REGULAR", ""}:
            raise ValueError("GTT supports only variety=regular")
        if validity and _u(validity) not in {"DAY", ""}:
            raise ValueError("GTT supports only validity=DAY")

        # Enforce required numeric fields
        if gtt_type == "SINGLE":
            trigger_price = _get(r, "trigger_price", "TriggerPrice")
            limit_price   = _get(r, "limit_price",   "LimitPrice")
            trigger_price = _float_or_err(trigger_price, "trigger_price")
            limit_price   = _float_or_err(limit_price,   "limit_price")
        else:
            tp1 = _float_or_err(_get(r, "trigger_price_1", "TriggerPrice1"), "trigger_price_1")
            lp1 = _float_or_err(_get(r, "limit_price_1",   "LimitPrice1"),   "limit_price_1")
            tp2 = _float_or_err(_get(r, "trigger_price_2", "TriggerPrice2"), "trigger_price_2")
            lp2 = _float_or_err(_get(r, "limit_price_2",   "LimitPrice2"),   "limit_price_2")
            if tp1 == tp2:
                raise ValueError("OCO trigger prices must differ")

        # Model compatibility: if order_type blank/invalid on GTT rows, use MARKET.
        if order_type_u not in ALLOWED_ORDER_TYPES:
            order_type_u = "MARKET"

        # price is irrelevant for GTT; ensure it's None
        price_out = None

        out = {
            # core
            "symbol": _s(symbol).upper(),
            "exchange": _u(exchange),
            "txn_type": _u(txn_type),
            "qty": qty,
            "order_type": order_type_u,
            "price": price_out,

            # enforce GTT-allowed values
            "product": "CNC",
            "validity": "DAY",
            "variety": "regular",

            # trigger fields
            "trigger_price": trigger_price if gtt_type == "SINGLE" else None,

            # extras
            "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
            "tag": _get(r, "tag", "Tag"),

            # GTT passthrough
            "gtt": "YES",
            "gtt_type": gtt_type,
            "limit_price":  (limit_price if gtt_type == "SINGLE" else None),
            "trigger_price_1": (tp1 if gtt_type == "OCO" else None),
            "limit_price_1":   (lp1 if gtt_type == "OCO" else None),
            "trigger_price_2": (tp2 if gtt_type == "OCO" else None),
            "limit_price_2":   (lp2 if gtt_type == "OCO" else None),
        }
        return out

    # -------------------------
    # Regular (non-GTT) branch
    # -------------------------
    if order_type_u not in ALLOWED_ORDER_TYPES:
        raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")

    price = _get(r, "price", "Price")
    if order_type_u in {"LIMIT", "SL", "SL-M"}:
        price = _float_or_err(price, "price")
    else:
        price = None  # MARKET ignores any price

    # For SL/SL-M regular orders, trigger_price is required; else ignore.
    trig = _get(r, "trigger_price", "TriggerPrice")
    if order_type_u in {"SL", "SL-M"}:
        trig = _float_or_err(trig, "trigger_price")
    else:
        trig = None

    out = {
        "symbol": _s(symbol).upper(),
        "exchange": _u(exchange),
        "txn_type": _u(txn_type),
        "qty": qty,
        "order_type": order_type_u,
        "price": price,
        "trigger_price": trig,
        "product": _get(r, "product", "Product") or "CNC",
        "validity": _get(r, "validity", "Validity") or "DAY",
        "variety": _get(r, "variety", "Variety") or "regular",
        "disclosed_qty": _get(r, "disclosed_qty", "DisclosedQty"),
        "tag": _get(r, "tag", "Tag"),

        # Ensure all GTT fields are blanked on regular orders
        "gtt": "",
        "gtt_type": "",
        "limit_price": None,
        "trigger_price_1": None,
        "limit_price_1": None,
        "trigger_price_2": None,
        "limit_price_2": None,
    }
    return out
