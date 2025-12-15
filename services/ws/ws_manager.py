# services/ws/ws_manager.py

from kiteconnect import KiteTicker

class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)

        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.on_order_update = self.on_order_update

    def start(self):
        self.kws.connect(threaded=True)

    def stop(self):
        try:
            self.kws.close()
        except Exception:
            pass

    def on_connect(self, ws, response):
        pass

    def on_close(self, ws, code, reason):
        pass

    def on_error(self, ws, code, reason):
        pass

    def on_order_update(self, ws, data):
        try:
            if data.get("transaction_type") != "BUY":
                return
            if data.get("status") != "COMPLETE":
                return

            order_id = data.get("order_id")
            filled_qty = data.get("filled_quantity", 0)

            if order_id and filled_qty:
                self.linker.credit_from_fill(order_id, filled_qty)

        except Exception:
            pass
