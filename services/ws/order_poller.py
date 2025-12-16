# services/ws/order_poller.py
"""
Fallback order status poller when WebSocket isn't delivering updates.
Polls order status via Kite API and credits linker when orders fill.
"""

import threading
import time
from typing import Optional

class OrderPoller:
    def __init__(self, kite, linker):
        """
        Args:
            kite: KiteConnect instance
            linker: OrderLinker instance
        """
        self.kite = kite
        self.linker = linker
        self._running = False
        self._thread = None
        self._known_orders = {}  # order_id -> last_status
        self._credited = set()   # already credited order_ids
        
    def start(self):
        """Start polling for order status."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print("[ORDER_POLLER] Started polling for order status")
    
    def stop(self):
        """Stop polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print("[ORDER_POLLER] Stopped")
    
    def track_order(self, order_id: str):
        """Add an order to the polling watch list."""
        if order_id not in self._known_orders:
            self._known_orders[order_id] = None
            print(f"[ORDER_POLLER] Tracking order {order_id}")
    
    def _poll_loop(self):
        """Main polling loop - runs every 2 seconds."""
        while self._running:
            try:
                self._check_orders()
            except Exception as e:
                print(f"[ORDER_POLLER] Poll error: {e}")
            
            time.sleep(2)
    
    def _check_orders(self):
        """Check status of all tracked orders."""
        if not self._known_orders:
            return
        
        try:
            orders = self.kite.orders()
            if not orders:
                return
            
            for order in orders:
                order_id = str(order.get("order_id"))
                status = order.get("status")
                txn_type = order.get("transaction_type")
                
                # Only care about BUY orders
                if txn_type != "BUY":
                    continue
                
                # Only track known orders
                if order_id not in self._known_orders:
                    continue
                
                # Check for COMPLETE status
                if status == "COMPLETE" and order_id not in self._credited:
                    filled_qty = order.get("filled_quantity", 0)
                    self._credited.add(order_id)
                    symbol = order.get("tradingsymbol", "?")
                    
                    print(f"[ORDER_POLLER] ✅ BUY COMPLETE (via API): {order_id} {symbol} qty={filled_qty}")
                    
                    # Credit the linker
                    self.linker.credit_by_order_id(order_id, filled_qty)
                    
                # Update status tracking
                if status != self._known_orders[order_id]:
                    self._known_orders[order_id] = status
                    print(f"[ORDER_POLLER] {order_id}: {status}")
        
        except Exception as e:
            print(f"[ORDER_POLLER] Error fetching orders: {e}")
    
    def snapshot(self):
        """Return current state."""
        return {
            "running": self._running,
            "tracked_orders": list(self._known_orders.keys()),
            "credited": list(self._credited),
            "status": dict(self._known_orders),
        }
