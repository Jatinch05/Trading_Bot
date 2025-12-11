from __future__ import annotations
from typing import Iterable, Any, Dict, List
import pandas as pd

def _get(obj: Any, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj: return obj[n]
        if hasattr(obj, n): return getattr(obj, n)
    return default

def _f(x, name): 
    try: return float(x)
    except: raise ValueError(f"{name} must be numeric")
def _i(x, name):
    try: return int(float(x))
    except: raise ValueError(f"{name} must be int")

def place_gtts_single(intents: Iterable[Any], kite) -> pd.DataFrame:
    res: List[Dict[str, Any]] = []
    for idx, it in enumerate(intents):
        row = {"idx": idx}
        try:
            if str(_get(it, "gtt", "GTT","")).upper() != "YES" or str(_get(it,"gtt_type","GTTType","SINGLE")).upper() != "SINGLE":
                continue
            symbol = str(_get(it, "symbol","tradingsymbol")).upper()
            exchange = str(_get(it, "exchange")).upper()
            txn = str(_get(it, "txn_type","transaction_type")).upper()
            qty = _i(_get(it, "qty","quantity"), "qty")
            tp = _f(_get(it, "trigger_price","TriggerPrice"), "trigger_price")
            lp = _f(_get(it, "limit_price","LimitPrice"), "limit_price")
            ltp = 0.0
            try:
                data = kite.ltp([f"{exchange}:{symbol}"])
                ltp = float(data[f"{exchange}:{symbol}"]["last_price"])
            except Exception:
                pass
            args = dict(
                trigger_type="single",
                tradingsymbol=symbol,
                exchange=exchange,
                trigger_values=[tp],
                last_price=ltp,
                orders=[{
                    "transaction_type": txn,
                    "quantity": qty,
                    "price": lp,
                    "order_type": "LIMIT",
                    "product": "NRML",
                }],
            )
            gid = kite.place_gtt(**args)
            row.update(symbol=symbol, exchange=exchange, txn=txn, qty=qty, gtt_type="SINGLE",
                       trigger_price=tp, limit_price=lp, last_price=ltp, product="NRML", ok=True, gtt_id=gid)
        except Exception as e:
            row.update(symbol=_get(it,"symbol","tradingsymbol",default=None),
                       exchange=_get(it,"exchange",default=None), gtt_type="SINGLE", ok=False, error=str(e))
        res.append(row)
    return pd.DataFrame(res)

def place_gtts_oco(intents: Iterable[Any], kite) -> pd.DataFrame:
    res: List[Dict[str, Any]] = []
    for idx, it in enumerate(intents):
        row = {"idx": idx}
        try:
            if str(_get(it, "gtt","GTT","")).upper() != "YES" or str(_get(it,"gtt_type","GTTType","")).upper() != "OCO":
                continue
            symbol = str(_get(it, "symbol","tradingsymbol")).upper()
            exchange = str(_get(it, "exchange")).upper()
            txn = str(_get(it, "txn_type","transaction_type")).upper()
            qty = _i(_get(it, "qty","quantity"), "qty")
            tp1 = _f(_get(it, "trigger_price_1","TriggerPrice1"), "trigger_price_1")
            lp1 = _f(_get(it, "limit_price_1","LimitPrice1"), "limit_price_1")
            tp2 = _f(_get(it, "trigger_price_2","TriggerPrice2"), "trigger_price_2")
            lp2 = _f(_get(it, "limit_price_2","LimitPrice2"), "limit_price_2")
            if tp1 == tp2: raise ValueError("OCO trigger prices must differ")
            ltp = 0.0
            try:
                data = kite.ltp([f"{exchange}:{symbol}"])
                ltp = float(data[f"{exchange}:{symbol}"]["last_price"])
            except Exception:
                pass
            args = dict(
                trigger_type="two-leg",
                tradingsymbol=symbol,
                exchange=exchange,
                trigger_values=[tp1, tp2],
                last_price=ltp,
                orders=[
                    {"transaction_type": txn, "quantity": qty, "price": lp1, "order_type": "LIMIT", "product": "NRML"},
                    {"transaction_type": txn, "quantity": qty, "price": lp2, "order_type": "LIMIT", "product": "NRML"},
                ],
            )
            gid = kite.place_gtt(**args)
            row.update(symbol=symbol, exchange=exchange, txn=txn, qty=qty, gtt_type="OCO",
                       trigger_price_1=tp1, limit_price_1=lp1, trigger_price_2=tp2, limit_price_2=lp2,
                       last_price=ltp, product="NRML", ok=True, gtt_id=gid)
        except Exception as e:
            row.update(symbol=_get(it,"symbol","tradingsymbol",default=None),
                       exchange=_get(it,"exchange",default=None), gtt_type="OCO", ok=False, error=str(e))
        res.append(row)
    return pd.DataFrame(res)
