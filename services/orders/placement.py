# services/orders/placement.py

def place_orders(kite, intents, linker=None):
    results = []

    for intent in intents:
        payload = intent.to_kite_payload()

        # Deferred SELL (linked / OCO)
        if payload is None:
            if linker:
                linker.queue_sell(intent)
            continue

        order_id = kite.place_order(**payload)

        results.append({
            "order_id": order_id,
            "symbol": intent.symbol,
            "txn_type": intent.txn_type,
            "qty": intent.qty,
        })

        # Register BUYs for crediting
        if linker and intent.txn_type == "BUY":
            linker.register_buy_order(
                order_id,
                intent.exchange,
                intent.symbol,
                intent.tag,
            )

    return results
