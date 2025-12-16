# services/ws/gtt_watcher.py

import threading
import time

from typing import Optional

class GTTWatcher:
    def __init__(self, kite):
        self.kite = kite
        self.running = False
        self.pending = set()
        self.resolved = {}
        self.interval = 2
        self._thread = None
        self._linker = None
        self._poller = None  # Fallback order status poller

    def bind_linker(self, linker):
        self._linker = linker
        # Seed pending from linker when binding
        if self._linker:
            for gid in self._linker.gtt_registry.keys():
                if gid not in self.resolved:
                    self.pending.add(str(gid))
        
        # Start fallback order poller
        if not self._poller and self._linker:
            from services.ws.order_poller import OrderPoller
            self._poller = OrderPoller(self.kite, self._linker)
            self._poller.start()

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            self._poll()
            time.sleep(self.interval)

    def _poll(self):
        try:
            # Ensure pending contains all known GTT BUY ids from linker
            if self._linker:
                for gid in self._linker.gtt_registry.keys():
                    if gid not in self.resolved:
                        self.pending.add(str(gid))

            for gtt in self.kite.get_gtts():
                gid = str(gtt["id"])
                if gid in self.pending and gtt["status"] == "triggered":
                    self.pending.remove(gid)
                    self.resolved[gid] = gtt.get("order_id")
                    print(f"[GTT_WATCHER] GTT triggered: {gid}")
                    # Bind child order to linker; WS will credit when child COMPLETES
                    if self._linker:
                        try:
                            orders = gtt.get("orders", [])
                            for o in orders:
                                # Extract child order_id from nested Zerodha response
                                result = o.get("result", {}) or {}
                                order_result = result.get("order_result", {}) or {}
                                child_oid = order_result.get("order_id")
                                if child_oid:
                                    # Map child order to same key as parent GTT
                                    self._linker.bind_gtt_child(gid, child_oid)
                                    print(f"[GTT_WATCHER] Bound child order: {child_oid} → parent GTT {gid}")
                                    
                                    # Also track it in the poller (fallback for WS failures)
                                    if self._poller:
                                        self._poller.track_order(child_oid)
                                        print(f"[GTT_WATCHER] Started polling child order {child_oid} (WS backup)")
                        except Exception as e:
                            print(f"[GTT_WATCHER] Error binding child: {e}")
        except Exception:
            pass

    def snapshot(self):
        return {
            "running": self.running,
            "bound": self._linker is not None,
            "pending": list(self.pending),
            "resolved": dict(self.resolved),
            "interval": self.interval,
        }
