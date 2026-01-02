# services/ws/ws_manager.py
from kiteconnect import KiteTicker
import threading
import datetime as _dt
import time

class WSManager:
    def __init__(self, api_key, access_token, linker):
        self.linker = linker
        self.kws = KiteTicker(api_key, access_token)
        self._credited_orders = set()
        self._events = []  # recent WS events for debugging
        self._connected = False
        self._connection_time = None
        self._stopped = False
        self.token_exchanged_at: float = None  # Set by runtime to track token age

        # KiteTicker stubs are sometimes typed as read-only; use setattr for compatibility.
        setattr(self.kws, "on_ticks", self.on_ticks)
        setattr(self.kws, "on_connect", self.on_connect)
        setattr(self.kws, "on_close", self.on_close)
        setattr(self.kws, "on_error", self.on_error)
        setattr(self.kws, "on_reconnect", self.on_reconnect)
        setattr(self.kws, "on_noreconnect", self.on_noreconnect)
        setattr(self.kws, "on_order_update", self.on_order_update)

    def start(self):
        self._log("WS start() called; connecting...")
        self._connection_time = _dt.datetime.now(_dt.timezone.utc).timestamp()
        self.kws.connect(threaded=True)
        
        # Monitor connection timeout (20 seconds)
        def check_timeout():
            threading.Event().wait(20)
            if not self._connected:
                self._log("[WS] ❌ Connection timeout after 20s - check token/network")
        
        threading.Thread(target=check_timeout, daemon=True).start()

    def stop(self):
        """Best-effort stop.

        Streamlit refresh can leave old daemon threads alive; call this when
        restarting workers or signing out.
        """
        self._stopped = True
        try:
            self.kws.close()
        except Exception:
            pass
        self._connected = False
        self._log("[WS] stop() called")

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
        self._connected = True
        now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
        elapsed = now_ts - self._connection_time if self._connection_time else 0
        self._log(f"✅ [WS] Connected in {elapsed:.1f}s; resp={resp}")
        try:
            ws.subscribe([])  # No instrument ticks needed; order updates are pushed globally
        except Exception as e:
            self._log(f"[WS] Subscribe error: {e}")

    def on_close(self, ws, code, reason):
        self._log(f"[WS] Closed code={code} reason={reason}")
        self._connected = False

    def on_error(self, ws, code, reason):
        self._log(f"❌ [WS] Error code={code} reason={reason}")
        self._log(f"   💡 If code=403: Token expired or invalid")
        self._log(f"   💡 If timeout: Network/firewall issue")

    def on_reconnect(self, ws, attempt_count):
        self._log(f"[WS] Reconnecting attempt={attempt_count}")

    def on_noreconnect(self, ws):
        self._log("[WS] No reconnect; giving up")

    def on_order_update(self, ws, data):
        # Log token age periodically if available
        if self.token_exchanged_at is not None:
            token_age_hours = (time.time() - self.token_exchanged_at) / 3600
            if token_age_hours > 23.5:
                self._log(f"[WS] ⏰ Token age: {token_age_hours:.1f}h (>24h expiry, consider new token)")
        
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
            "connected": self._connected,
            "stopped": self._stopped,
        }
