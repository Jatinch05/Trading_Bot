# services/validation/validate.py — robust normalization & validation (NRML-only)

import pandas as pd
from typing import Tuple, List
from models import OrderIntent

REQUIRED_COLS = [
    "symbol","exchange","txn_type","qty",
    "order_type","price","trigger_price",
    "product","validity","variety",
    "disclosed_qty","tag",
    "gtt","gtt_type",
    "gtt_trigger","gtt_limit",
    "gtt_trigger_1","gtt_limit_1",
    "gtt_trigger_2","gtt_limit_2",
]

ALLOWED_ORDER_TYPES = {"MARKET","LIMIT","SL","SL-M"}

def _safe(v):
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v

def _to_float_or_none(v):
    v = _safe(v)
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return float(f)
    except Exception:
        return None

def _to_int_or_none(v):
    v = _safe(v)
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return int(f)
    except Exception:
        return None

def _norm_tag(tag: str | None) -> str | None:
    """Normalize to 'link:<group>' if present; else None."""
    if not tag:
        return None
    t = str(tag).strip()
    if not t:
        return None
    tl = t.lower()
    if tl.startswith("link:"):
        group = tl.split(":", 1)[1].strip()
        if not group:
            raise ValueError("Invalid tag; 'link:<group>' requires a group value")
        return f"link:{group}"
    # Disallow any other tag pattern
    raise ValueError("Invalid tag; only 'link:<group>' allowed")

def _require(v, msg: str):
    if v in (None, "", 0, 0.0):
        raise ValueError(msg)
    return v

def normalize_and_validate(df: pd.DataFrame, instruments) -> Tuple[List[OrderIntent], pd.DataFrame, List[Tuple[int, str]]]:
    errors, intents, validated_rows = [], [], []

    # Ensure columns exist
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = None

    # Accept alias headings from your template (carry over if target missing or NaN)
    if "limit_price" in df.columns and ("gtt_limit" not in df.columns or df["gtt_limit"].isna().all()):
        df["gtt_limit"] = df.get("gtt_limit", df["limit_price"]).fillna(df["limit_price"])
    if "trigger_price" in df.columns and ("gtt_trigger" not in df.columns or df["gtt_trigger"].isna().all()):
        df["gtt_trigger"] = df.get("gtt_trigger", df["trigger_price"]).fillna(df["trigger_price"])
    if "trigger_price_1" in df.columns and ("gtt_trigger_1" not in df.columns or df["gtt_trigger_1"].isna().all()):
        df["gtt_trigger_1"] = df.get("gtt_trigger_1", df["trigger_price_1"]).fillna(df["trigger_price_1"])
    if "limit_price_1" in df.columns and ("gtt_limit_1" not in df.columns or df["gtt_limit_1"].isna().all()):
        df["gtt_limit_1"] = df.get("gtt_limit_1", df["limit_price_1"]).fillna(df["limit_price_1"])
    if "trigger_price_2" in df.columns and ("gtt_trigger_2" not in df.columns or df["gtt_trigger_2"].isna().all()):
        df["gtt_trigger_2"] = df.get("gtt_trigger_2", df["trigger_price_2"]).fillna(df["trigger_price_2"])
    if "limit_price_2" in df.columns and ("gtt_limit_2" not in df.columns or df["gtt_limit_2"].isna().all()):
        df["gtt_limit_2"] = df.get("gtt_limit_2", df["limit_price_2"]).fillna(df["limit_price_2"])

    for idx, row in df.iterrows():
        try:
            # Basic fields
            symbol = str(_safe(row["symbol"]) or "").strip().upper()
            exchange = str(_safe(row["exchange"]) or "").strip().upper()
            if not symbol:
                raise ValueError("symbol is required")
            if not exchange:
                raise ValueError("exchange is required")

            # Instrument validation explicitly skipped (per design)

            qty = _to_int_or_none(row["qty"])
            if not qty or qty <= 0:
                raise ValueError("qty must be > 0")

            txn_type = str(_safe(row["txn_type"]) or "").upper()
            if txn_type not in ("BUY","SELL"):
                raise ValueError("txn_type must be BUY or SELL")

            # NRML-only enforced
            product = "NRML"
            validity = "DAY"
            variety = "regular"

            # GTT flags
            gtt_flag = "YES" if str(_safe(row["gtt"]) or "").upper() == "YES" else "NO"
            gtt_type = str(_safe(row["gtt_type"]) or "").upper()

            # Order type validation (for regular orders only)
            order_type = str(_safe(row["order_type"]) or "").upper() or "MARKET"
            if order_type not in ALLOWED_ORDER_TYPES:
                raise ValueError(f"order_type must be one of {sorted(ALLOWED_ORDER_TYPES)}")

            price = _to_float_or_none(row["price"])
            trig_regular = _to_float_or_none(row["trigger_price"])  # for regular SL/SL-M only
            disclosed_qty = _to_int_or_none(row["disclosed_qty"]) or 0

            # Tag normalization (only link:* allowed)
            tag = _norm_tag((str(_safe(row["tag"]) or "").strip()) or None)

            if gtt_flag == "YES":
                # GTT intents ignore 'order_type' semantics; child order created as LIMIT by GTT API
                if gtt_type == "SINGLE":
                    trig_s = _to_float_or_none(row["gtt_trigger"])
                    limit_s = _to_float_or_none(row["gtt_limit"])
                    _require(trig_s, "GTT SINGLE requires trigger")
                    _require(limit_s, "GTT SINGLE requires limit")

                    intent = OrderIntent(
                        exchange=exchange, symbol=symbol, txn_type=txn_type, qty=qty,
                        order_type=order_type, price=price, trigger_price=trig_regular,
                        product=product, validity=validity, variety=variety,
                        disclosed_qty=disclosed_qty, tag=tag,
                        gtt="YES", gtt_type="SINGLE",
                        gtt_trigger=trig_s, gtt_limit=limit_s,
                    )

                elif gtt_type == "OCO":
                    trig1 = _to_float_or_none(row["gtt_trigger_1"])
                    limit1 = _to_float_or_none(row["gtt_limit_1"])
                    trig2 = _to_float_or_none(row["gtt_trigger_2"])
                    limit2 = _to_float_or_none(row["gtt_limit_2"])
                    _require(trig1, "GTT OCO leg 1 trigger required")
                    _require(limit1, "GTT OCO leg 1 limit required")
                    _require(trig2, "GTT OCO leg 2 trigger required")
                    _require(limit2, "GTT OCO leg 2 limit required")

                    intent = OrderIntent(
                        exchange=exchange, symbol=symbol, txn_type=txn_type, qty=qty,
                        order_type=order_type, price=price, trigger_price=trig_regular,
                        product=product, validity=validity, variety=variety,
                        disclosed_qty=disclosed_qty, tag=tag,
                        gtt="YES", gtt_type="OCO",
                        gtt_trigger_1=trig1, gtt_limit_1=limit1,
                        gtt_trigger_2=trig2, gtt_limit_2=limit2,
                    )
                else:
                    raise ValueError("gtt_type must be SINGLE or OCO when gtt=YES")

            else:
                # Regular order semantic checks
                if order_type == "MARKET":
                    if trig_regular not in (None, "", 0, 0.0):
                        raise ValueError("MARKET order must not include trigger_price")
                    # price ignored for MARKET

                elif order_type == "LIMIT":
                    _require(price, "LIMIT order requires price")
                    if trig_regular not in (None, "", 0, 0.0):
                        # allow user-supplied trigger field to exist but must not be set for LIMIT
                        raise ValueError("LIMIT order must not include trigger_price")

                elif order_type in ("SL","SL-M"):
                    _require(trig_regular, f"{order_type} requires trigger_price")
                    # SL requires price; SL-M must not have price
                    if order_type == "SL":
                        _require(price, "SL order requires price")
                    else:  # SL-M
                        if price not in (None, "", 0, 0.0):
                            raise ValueError("SL-M must not include price")

                intent = OrderIntent(
                    exchange=exchange, symbol=symbol, txn_type=txn_type, qty=qty,
                    order_type=order_type, price=price, trigger_price=trig_regular,
                    product=product, validity=validity, variety=variety,
                    disclosed_qty=disclosed_qty, tag=tag,
                    gtt="NO", gtt_type=None
                )

            intents.append(intent)
            validated_rows.append(intent.model_dump())

        except Exception as e:
            errors.append((idx, str(e)))

    vdf = pd.DataFrame(validated_rows)
    return intents, vdf, errors
