# services/ws/ws_manager.py
from kiteconnect import KiteTicker

class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = lambda ws, resp: ws.subscribe([])
        self.kws.on_order_update = self.on_order_update

    def start(self):
        self.kws.connect(threaded=True)

    def on_ticks(self, ws, ticks):
        pass

    def on_order_update(self, ws, data):
        if data["status"] == "COMPLETE":
            self.linker.on_buy_fill(
                data["order_id"],
                data.get("filled_quantity", 0),
            )
