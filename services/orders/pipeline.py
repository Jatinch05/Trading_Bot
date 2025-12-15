# services/orders/pipeline.py

from services.orders.placement import place_orders


def execute_bundle(kite, intents, linker):
    def _release_sells(sell_intents):
        place_orders(kite, sell_intents, linker=None)

    linker.set_release_callback(_release_sells)
    return place_orders(kite, intents, linker=linker)
