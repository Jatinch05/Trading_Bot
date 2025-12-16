# services/ws/ws_manager.py
from kiteconnect import KiteTicker

class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)
        self._credited_orders = set()
        self._events = []  # recent WS events for debugging

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.on_reconnect = self.on_reconnect
        self.kws.on_noreconnect = self.on_noreconnect
        self.kws.on_order_update = self.on_order_update

    def start(self):
        self._log("WS start() called; connecting...")
        self.kws.connect(threaded=True)

    # -------------------------------------------------
    # Event helpers
    # -------------------------------------------------
    def _log(self, msg):
        print(msg)
        try:
            self._events.append(msg)
            if len(self._events) > 50:
                self._events.pop(0)
        except Exception:
            pass

    def on_ticks(self, ws, ticks):
        pass

    def on_connect(self, ws, resp):
        self._log(f"[WS] Connected; resp={resp}")
        try:
            ws.subscribe([])  # No instrument ticks needed; order updates are pushed globally
        except Exception as e:
            self._log(f"[WS] Subscribe error: {e}")

    def on_close(self, ws, code, reason):
        self._log(f"[WS] Closed code={code} reason={reason}")

    def on_error(self, ws, code, reason):
        self._log(f"[WS] Error code={code} reason={reason}")

    def on_reconnect(self, ws, attempt_count):
        self._log(f"[WS] Reconnecting attempt={attempt_count}")

    def on_noreconnect(self, ws):
        self._log("[WS] No reconnect; giving up")

    def on_order_update(self, ws, data):
        # Credit linker only for BUY-side completes to release SELLs
        if data.get("status") == "COMPLETE" and data.get("transaction_type") == "BUY":
            oid = data.get("order_id")
            if not oid:
                return
            # Deduplicate COMPLETE events per order_id
            if oid in self._credited_orders:
                self._log(f"[WS] Duplicate COMPLETE for {oid}, skipping")
                return
            self._credited_orders.add(oid)
            filled_qty = data.get("filled_quantity", 0)
            self._log(f"[WS] BUY COMPLETE: {oid} filled={filled_qty} symbol={data.get('tradingsymbol')}")
            self.linker.on_buy_fill(oid, filled_qty)

    def snapshot(self):
        return {
            "events": list(self._events),
            "credited_orders": list(self._credited_orders),
        }
