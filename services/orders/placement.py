# services/orders/placement.py

from typing import List
from models import OrderIntent


def place_orders(kite, intents: List[OrderIntent], linker=None):
    """
    Places BUY orders immediately.
    SELL orders:
      - If GTT -> placed ONLY via place_gtt
      - If non-GTT -> queued via linker or placed directly
    """

    results = []

    for intent in intents:
        # -----------------------------
        # BUY ORDERS (always normal)
        # -----------------------------
        if intent.txn_type == "BUY":
            payload = intent.to_kite_payload()
            order_id = kite.place_order(**payload)

            results.append({
                "order_id": order_id,
                "symbol": intent.symbol,
                "txn_type": "BUY",
                "qty": intent.qty,
                "status": "placed",
            })

            # Register BUY with linker if needed
            if linker and intent.tag and intent.tag.startswith("link:"):
                linker.register_buy(order_id, intent)

            continue

        # -----------------------------
        # SELL ORDERS — GTT SINGLE
        # -----------------------------
        if intent.txn_type == "SELL" and intent.gtt == "YES":
            # 🚫 ABSOLUTE GUARANTEE: never use place_order
            if intent.gtt_type != "SINGLE":
                raise ValueError(f"Unsupported GTT type: {intent.gtt_type}")

            if intent.gtt_trigger is None or intent.gtt_limit is None:
                raise ValueError("GTT SINGLE requires gtt_trigger and gtt_limit")

            trigger = float(intent.gtt_trigger)
            price = float(intent.gtt_limit)

            response = kite.place_gtt(
                trigger_type="single",
                tradingsymbol=intent.symbol,
                exchange=intent.exchange,
                trigger_values=[trigger],
                last_price=trigger,
                orders=[{
                    "transaction_type": "SELL",
                    "quantity": intent.qty,
                    "order_type": "LIMIT",
                    "product": intent.product,
                    "price": price,
                }],
            )

            results.append({
                "order_id": response["id"],
                "symbol": intent.symbol,
                "txn_type": "SELL",
                "qty": intent.qty,
                "status": "gtt_placed",
                "trigger": trigger,
                "limit": price,
            })

            continue

        # -----------------------------
        # SELL ORDERS — NON-GTT
        # -----------------------------
        if intent.txn_type == "SELL":
            if linker and intent.tag and intent.tag.startswith("link:"):
                linker.queue_sell(intent)
                results.append({
                    "order_id": None,
                    "symbol": intent.symbol,
                    "txn_type": "SELL",
                    "qty": intent.qty,
                    "status": "queued",
                })
                continue

            payload = intent.to_kite_payload()
            order_id = kite.place_order(**payload)

            results.append({
                "order_id": order_id,
                "symbol": intent.symbol,
                "txn_type": "SELL",
                "qty": intent.qty,
                "status": "placed",
            })

            continue

        raise ValueError(f"Unknown txn_type: {intent.txn_type}")

    return results
