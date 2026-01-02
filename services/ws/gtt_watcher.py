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
        self.token_exchanged_at: float = None  # Set by runtime to track token age

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
        
        # Scan for already-triggered GTTs (startup recovery)
        self._scan_existing_triggered_gtts()

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        try:
            if self._poller:
                self._poller.stop()
        except Exception:
            pass

    def _loop(self):
        while self.running:
            self._poll()
            time.sleep(self.interval)

    def _poll(self):
        try:
            # Log token age periodically if available
            if self.token_exchanged_at is not None:
                token_age_hours = (time.time() - self.token_exchanged_at) / 3600
                if token_age_hours > 23.5:
                    print(f"[GTT_WATCHER] ⏰ Token age: {token_age_hours:.1f}h (>24h expiry, consider new token)")
            
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
                            print(f"[GTT_WATCHER] GTT {gid} has {len(orders)} orders in response")
                            extracted_any = False
                            for o in orders:
                                print(f"[GTT_WATCHER] Order object: {o}")
                                # Extract child order_id from nested Zerodha response
                                result = o.get("result", {}) or {}
                                order_result = result.get("order_result", {}) or {}
                                child_oid = order_result.get("order_id")
                                print(f"[GTT_WATCHER] Extracted child_oid={child_oid}")
                                if child_oid:
                                    extracted_any = True
                                    child_oid = str(child_oid)  # Ensure string
                                    # Map child order to same key as parent GTT
                                    self._linker.bind_gtt_child(gid, child_oid)
                                    print(f"[GTT_WATCHER] Bound child order: {child_oid} → parent GTT {gid}")
                                    # Also track it in the poller (fallback for WS failures)
                                    if self._poller:
                                        self._poller.track_order(child_oid)
                                        print(f"[GTT_WATCHER] Started polling child order {child_oid} (WS backup)")
                                    else:
                                        print(f"[GTT_WATCHER] ⚠️  Poller not ready yet for {child_oid}")
                            # Fallback: some SDKs put child order id directly at top-level
                            if not extracted_any:
                                direct_child = gtt.get("order_id")
                                if direct_child:
                                    direct_child = str(direct_child)
                                    self._linker.bind_gtt_child(gid, direct_child)
                                    print(f"[GTT_WATCHER] Fallback bound direct child {direct_child} for GTT {gid}")
                                    if self._poller:
                                        self._poller.track_order(direct_child)
                                        print(f"[GTT_WATCHER] Started polling direct child {direct_child}")
                        except Exception as e:
                            import traceback
                            print(f"[GTT_WATCHER] Error binding child: {e}")
                            traceback.print_exc()
        except Exception:
            pass
    
    def _scan_existing_triggered_gtts(self):
        """Scan for GTTs that were already triggered before watcher started."""
        if not self._linker:
            return
        
        print("[GTT_WATCHER] Scanning for already-triggered GTTs...")
        try:
            all_gtts = self.kite.get_gtts()
            for gtt in all_gtts:
                gid = str(gtt["id"])
                
                # Only check GTTs we're tracking
                if gid not in self._linker.gtt_registry:
                    continue
                
                # If already resolved, try to bind null children on recovery scan
                if gid in self.resolved:
                    if self.resolved[gid] is None and gtt.get("order_id"):
                        direct_child = str(gtt.get("order_id"))
                        self._linker.bind_gtt_child(gid, direct_child)
                        print(f"[GTT_WATCHER] Recovery: Bound null→child {direct_child} for GTT {gid}")
                        if self._poller:
                            self._poller.track_order(direct_child)
                    continue
                
                # Check if triggered
                if gtt["status"] == "triggered":
                    print(f"[GTT_WATCHER] Found pre-existing trigger: {gid}")
                    self.pending.discard(gid)
                    self.resolved[gid] = gtt.get("order_id")
                    
                    # Extract and track child orders
                    try:
                        orders = gtt.get("orders", [])
                        print(f"[GTT_WATCHER] GTT {gid} has {len(orders)} orders in response")
                        recovered_any = False
                        for o in orders:
                            result = o.get("result", {}) or {}
                            order_result = result.get("order_result", {}) or {}
                            child_oid = order_result.get("order_id")
                            if child_oid:
                                recovered_any = True
                                child_oid = str(child_oid)
                                self._linker.bind_gtt_child(gid, child_oid)
                                print(f"[GTT_WATCHER] Recovered child order: {child_oid} → GTT {gid}")
                                
                                if self._poller:
                                    self._poller.track_order(child_oid)
                                    print(f"[GTT_WATCHER] Started polling recovered child {child_oid}")
                        if not recovered_any:
                            direct_child = gtt.get("order_id")
                            if direct_child:
                                direct_child = str(direct_child)
                                self._linker.bind_gtt_child(gid, direct_child)
                                print(f"[GTT_WATCHER] Fallback recovered direct child {direct_child} for GTT {gid}")
                                if self._poller:
                                    self._poller.track_order(direct_child)
                                    print(f"[GTT_WATCHER] Started polling recovered direct child {direct_child}")
                    except Exception as e:
                        print(f"[GTT_WATCHER] Error recovering child orders: {e}")
                        
            print("[GTT_WATCHER] Startup scan complete")
        except Exception as e:
            print(f"[GTT_WATCHER] Startup scan error: {e}")

    def snapshot(self):
        return {
            "running": self.running,
            "bound": self._linker is not None,
            "pending": list(self.pending),
            "resolved": dict(self.resolved),
            "interval": self.interval,
            "poller": self._poller.snapshot() if self._poller else None,
        }
