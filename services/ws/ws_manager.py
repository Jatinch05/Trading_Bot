# services/ws/ws_manager.py
from kiteconnect import KiteTicker

class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)
        self._credited_orders = set()

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = lambda ws, resp: ws.subscribe([])
        self.kws.on_order_update = self.on_order_update

    def start(self):
        self.kws.connect(threaded=True)

    def on_ticks(self, ws, ticks):
        pass

    def on_order_update(self, ws, data):
        # Credit linker only for BUY-side completes to release SELLs
        if data.get("status") == "COMPLETE" and data.get("transaction_type") == "BUY":
            oid = data.get("order_id")
            if not oid:
                return
            # Deduplicate COMPLETE events per order_id
            if oid in self._credited_orders:
                print(f"[WS] Duplicate COMPLETE for {oid}, skipping")
                return
            self._credited_orders.add(oid)
            filled_qty = data.get("filled_quantity", 0)
            print(f"[WS] BUY COMPLETE: {oid} filled={filled_qty} symbol={data.get('tradingsymbol')}")
            self.linker.on_buy_fill(oid, filled_qty)
