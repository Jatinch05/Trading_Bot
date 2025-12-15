# services/orders/pipeline.py
from services.orders.placement import (
    place_regular_order,
    place_gtt_single,
    place_gtt_oco,
)

def execute_bundle(*, kite, intents, linker=None):
    """
    BUY intents: placed immediately.
    SELL intents: always queued.
    SELL placement happens ONLY via linker release callback.
    """

    results = []

    # STRICT sell release path (no recursion)
    def _release_sells(sells):
        for intent in sells:
            if intent.gtt == "YES":
                if intent.gtt_type == "SINGLE":
                    oid = place_gtt_single(kite, intent)
                else:
                    oid = place_gtt_oco(kite, intent)
            else:
                oid = place_regular_order(kite, intent)

            results.append({
                "order_id": oid,
                "symbol": intent.symbol,
                "released": True,
            })

    if linker:
        linker.set_release_callback(_release_sells)

    for intent in intents:
        # RULE: ALL SELLs are queued, never placed here
        if intent.txn_type == "SELL":
            linker.queue_sell(intent)
            results.append({
                "status": "QUEUED",
                "symbol": intent.symbol,
            })
            continue

        # BUY placement (IMMEDIATE)
        if intent.gtt == "YES":
            if intent.gtt_type == "SINGLE":
                oid = place_gtt_single(kite, intent)
            else:
                oid = place_gtt_oco(kite, intent)
        else:
            oid = place_regular_order(kite, intent)

        results.append({
            "order_id": oid,
            "symbol": intent.symbol,
        })

        # BUY must be registered for SELL release
        if linker:
            linker.register_buy(oid, intent)

    return results
