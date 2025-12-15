# services/orders/placement.py

def place_orders(kite, intents, linker=None):
    results = []

    for intent in intents:
        payload = intent.to_kite_payload()
        order_id = kite.place_order(**payload)

        results.append({
            "order_id": order_id,
            "symbol": intent.symbol,
            "txn_type": intent.txn_type,
            "qty": intent.qty,
        })

        if linker and intent.txn_type == "BUY":
            linker.register_buy_order(
                order_id,
                intent.exchange,
                intent.symbol,
                intent.tag,
            )

    return results
