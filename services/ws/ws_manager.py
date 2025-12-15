# services/ws/ws_manager.py
from kiteconnect import KiteTicker


class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)
        self.kws.on_order_update = self.on_order_update

    def start(self):
        self.kws.connect(threaded=True)

    def on_order_update(self, ws, data):
        try:
            if data.get("transaction_type") != "BUY":
                return
            if data.get("status") != "COMPLETE":
                return
            self.linker.credit_from_fill(
                data["order_id"],
                data["filled_quantity"],
            )
        except Exception:
            pass
