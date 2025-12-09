# services/gtt.py
from __future__ import annotations

from typing import Iterable, Any, Dict, List
import pandas as pd

# If you already have place_gtts_single in this file, keep it as-is.
# The implementation below is compatible with your OrderIntent schema and harmless if identical.

def _first(*vals, default=None):
    for v in vals:
        if v is not None and v != "":
            return v
    return default

def _get_attr(obj: Any, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default

def _as_float(val, name):
    try:
        return float(val)
    except Exception:
        raise ValueError(f"{name} must be numeric")

def _as_int(val, name):
    try:
        return int(float(val))
    except Exception:
        raise ValueError(f"{name} must be an integer")

def place_gtts_single(intents: Iterable[Any], kite) -> pd.DataFrame:
    """
    Create SINGLE-leg GTTs.
    Expected on each intent:
      - symbol, exchange, txn_type (BUY/SELL), qty
      - gtt == 'YES', gtt_type == 'SINGLE'
      - trigger_price, limit_price
      - last_price optional (we try kite.ltp if missing)
    Returns DataFrame with results.
    """
    results: List[Dict[str, Any]] = []

    for idx, it in enumerate(intents):
        row = {"idx": idx}
        try:
            gtt = str(_get_attr(it, "gtt", "GTT", default="")).upper()
            gtt_type = str(_get_attr(it, "gtt_type", "GTTType", default="")).upper() or "SINGLE"
            if gtt != "YES" or gtt_type != "SINGLE":
                continue

            symbol = _first(_get_attr(it, "symbol", "tradingsymbol", "Tradingsymbol"))
            exchange = _first(_get_attr(it, "exchange", "Exchange"))
            if not symbol or not exchange:
                raise ValueError("Missing symbol or exchange")

            txn = str(_first(_get_attr(it, "txn_type", "transaction_type", "TransactionType"))).upper()
            if txn not in {"BUY", "SELL"}:
                raise ValueError("txn_type must be BUY or SELL")

            qty = _as_int(_first(_get_attr(it, "qty", "quantity", "Quantity")), "qty")
            if qty < 1:
                raise ValueError("qty must be >= 1")

            trigger_price = _as_float(_get_attr(it, "trigger_price", "TriggerPrice"), "trigger_price")
            limit_price = _as_float(_get_attr(it, "limit_price", "LimitPrice"), "limit_price")

            last_price = _get_attr(it, "last_price", "LastPrice")
            if last_price is None:
                try:
                    ltp = kite.ltp([f"{exchange}:{symbol}"])
                    last_price = float(ltp[f"{exchange}:{symbol}"]["last_price"])
                except Exception:
                    last_price = 0.0

            args = dict(
                trigger_type="single",
                tradingsymbol=str(symbol).upper(),
                exchange=str(exchange).upper(),
                trigger_values=[trigger_price],
                last_price=float(last_price),
                orders=[{
                    "transaction_type": txn,
                    "quantity": qty,
                    "price": limit_price,
                    "order_type": "LIMIT",
                    "product": "CNC",
                }],
            )

            gtt_id = kite.place_gtt(**args)
            row.update({
                "symbol": symbol,
                "exchange": exchange,
                "txn": txn,
                "qty": qty,
                "gtt_type": "SINGLE",
                "trigger_price": trigger_price,
                "limit_price": limit_price,
                "last_price": last_price,
                "ok": True,
                "gtt_id": gtt_id,
            })
        except Exception as e:
            row.update({
                "symbol": _get_attr(it, "symbol", "tradingsymbol", "Tradingsymbol", default=None),
                "exchange": _get_attr(it, "exchange", "Exchange", default=None),
                "gtt_type": "SINGLE",
                "ok": False,
                "error": str(e),
            })

        results.append(row)

    return pd.DataFrame(results)


def place_gtts_oco(intents: Iterable[Any], kite) -> pd.DataFrame:
    """
    Create OCO (two-leg) GTTs.
    Expected on each intent:
      - symbol, exchange, txn_type (BUY/SELL), qty
      - gtt == 'YES', gtt_type == 'OCO'
      - trigger_price_1, limit_price_1, trigger_price_2, limit_price_2
      - last_price optional (we try kite.ltp if missing)
    Returns DataFrame with results.
    """
    results: List[Dict[str, Any]] = []

    for idx, it in enumerate(intents):
        row = {"idx": idx}
        try:
            gtt = str(_get_attr(it, "gtt", "GTT", default="")).upper()
            gtt_type = str(_get_attr(it, "gtt_type", "GTTType", default="")).upper() or "SINGLE"
            if gtt != "YES" or gtt_type != "OCO":
                continue

            symbol = _first(_get_attr(it, "symbol", "tradingsymbol", "Tradingsymbol"))
            exchange = _first(_get_attr(it, "exchange", "Exchange"))
            if not symbol or not exchange:
                raise ValueError("Missing symbol or exchange")

            txn = str(_first(_get_attr(it, "txn_type", "transaction_type", "TransactionType"))).upper()
            if txn not in {"BUY", "SELL"}:
                raise ValueError("txn_type must be BUY or SELL")

            qty = _as_int(_first(_get_attr(it, "qty", "quantity", "Quantity")), "qty")
            if qty < 1:
                raise ValueError("qty must be >= 1")

            tp1 = _as_float(_get_attr(it, "trigger_price_1", "TriggerPrice1"), "trigger_price_1")
            lp1 = _as_float(_get_attr(it, "limit_price_1", "LimitPrice1"), "limit_price_1")
            tp2 = _as_float(_get_attr(it, "trigger_price_2", "TriggerPrice2"), "trigger_price_2")
            lp2 = _as_float(_get_attr(it, "limit_price_2", "LimitPrice2"), "limit_price_2")
            if tp1 == tp2:
                raise ValueError("OCO trigger prices must differ")

            last_price = _get_attr(it, "last_price", "LastPrice")
            if last_price is None:
                try:
                    ltp = kite.ltp([f"{exchange}:{symbol}"])
                    last_price = float(ltp[f"{exchange}:{symbol}"]["last_price"])
                except Exception:
                    last_price = 0.0

            args = dict(
                trigger_type="two-leg",
                tradingsymbol=str(symbol).upper(),
                exchange=str(exchange).upper(),
                trigger_values=[tp1, tp2],
                last_price=float(last_price),
                orders=[
                    {
                        "transaction_type": txn,
                        "quantity": qty,
                        "price": lp1,
                        "order_type": "LIMIT",
                        "product": "CNC",
                    },
                    {
                        "transaction_type": txn,
                        "quantity": qty,
                        "price": lp2,
                        "order_type": "LIMIT",
                        "product": "CNC",
                    },
                ],
            )

            gtt_id = kite.place_gtt(**args)
            row.update({
                "symbol": symbol,
                "exchange": exchange,
                "txn": txn,
                "qty": qty,
                "gtt_type": "OCO",
                "trigger_price_1": tp1,
                "limit_price_1": lp1,
                "trigger_price_2": tp2,
                "limit_price_2": lp2,
                "last_price": last_price,
                "ok": True,
                "gtt_id": gtt_id,
            })

        except Exception as e:
            row.update({
                "symbol": _get_attr(it, "symbol", "tradingsymbol", "Tradingsymbol", default=None),
                "exchange": _get_attr(it, "exchange", "Exchange", default=None),
                "gtt_type": "OCO",
                "ok": False,
                "error": str(e),
            })

        results.append(row)

    return pd.DataFrame(results)
