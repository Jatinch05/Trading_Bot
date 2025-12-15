# services/orders/pipeline.py

from services.orders.placement import place_orders, place_released_sells


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
        live=live,
    )


def execute_released_sells(*, sells, kite, live=True):
    """
    Place SELL intents that have been released by the linker.
    This function must NOT re-queue — it only places.
    """
    return place_released_sells(kite=kite, sells=sells, live=live)
