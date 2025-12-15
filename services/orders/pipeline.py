# services/orders/pipeline.py

from services.orders.placement import place_orders


def execute_bundle(*, intents, kite, linker=None, live=True):
    """
    Executes a bundle of intents.
    - BUYs placed immediately
    - SELLs queued or GTT-placed
    - linker decides WHEN sells are released
    """

    if not live:
        return [
            {
                "order_id": None,
                "symbol": i.symbol,
                "txn_type": i.txn_type,
                "qty": i.qty,
                "status": "dry_run",
            }
            for i in intents
        ]

    return place_orders(
        kite=kite,
        intents=intents,
        linker=linker,
    )
