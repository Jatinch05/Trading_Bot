from typing import List
import pandas as pd
from kiteconnect import KiteConnect

from models import OrderIntent
from services.ws import linker as ws_linker
from services.ws import gtt_watcher


def _get_ltp(kite, exchange: str, symbol: str) -> float:
    key = f"{exchange}:{symbol}"
    data = kite.ltp([key])
    return float(data[key]["last_price"])


def place_gtts(intents: List[OrderIntent], kite) -> pd.DataFrame:
    """
    Sole authority for ALL GTT placement (BUY & SELL).
    """
    rows = []

    for it in intents:
        try:
            ltp = _get_ltp(kite, it.exchange, it.symbol)

            if it.gtt_type == "SINGLE":
                resp = kite.place_gtt(
                    trigger_type=KiteConnect.GTT_TYPE_SINGLE,
                    tradingsymbol=it.symbol,
                    exchange=it.exchange,
                    trigger_values=[float(it.gtt_trigger)],
                    last_price=ltp,
                    orders=[{
                        "transaction_type": it.txn_type,
                        "quantity": int(it.qty),
                        "order_type": "LIMIT",
                        "price": float(it.gtt_limit),
                        "product": "NRML",
                    }],
                )

            elif it.gtt_type == "OCO":
                resp = kite.place_gtt(
                    trigger_type=KiteConnect.GTT_TYPE_OCO,
                    tradingsymbol=it.symbol,
                    exchange=it.exchange,
                    trigger_values=[
                        float(it.gtt_trigger_1),
                        float(it.gtt_trigger_2),
                    ],
                    last_price=ltp,
                    orders=[
                        {
                            "transaction_type": it.txn_type,
                            "quantity": int(it.qty),
                            "order_type": "LIMIT",
                            "price": float(it.gtt_limit_1),
                            "product": "NRML",
                        },
                        {
                            "transaction_type": it.txn_type,
                            "quantity": int(it.qty),
                            "order_type": "LIMIT",
                            "price": float(it.gtt_limit_2),
                            "product": "NRML",
                        },
                    ],
                )
            else:
                raise ValueError("Invalid gtt_type")

            trigger_id = resp.get("trigger_id")

            # Register BUY-side GTTs for linking
            if it.txn_type == "BUY" and it.tag:
                ws_linker.register_gtt_trigger(
                    trigger_id, it.exchange, it.symbol, it.tag
                )
                gtt_watcher.add_trigger(trigger_id)

            rows.append({
                "kind": "GTT",
                "exchange": it.exchange,
                "symbol": it.symbol,
                "side": it.txn_type,
                "qty": it.qty,
                "trigger_id": trigger_id,
                "status": "OK",
            })

        except Exception as e:
            rows.append({
                "kind": "GTT",
                "exchange": it.exchange,
                "symbol": it.symbol,
                "side": it.txn_type,
                "qty": it.qty,
                "status": "ERROR",
                "error": str(e),
            })

    return pd.DataFrame(rows)
