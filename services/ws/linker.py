# services/ws/linker.py
from collections import defaultdict, deque
import threading
from typing import Optional

class OrderLinker:
    def __init__(self):
        self.buy_credits = defaultdict(int)     # key -> filled qty
        self.sell_queues = defaultdict(deque)   # key -> deque[OrderIntent]
        self.buy_registry = {}                  # order_id -> key
        self.gtt_registry = {}                  # gtt_id -> key (for pending GTT BUYs)
        self._release_cb = None
        # Shared dedup across ALL credit sources (WS + poller)
        self._credited_order_ids = set()
        # Protect credits/queues/releases across background threads
        self._lock = threading.Lock()

    def set_release_callback(self, cb):
        self._release_cb = cb

    def _key(self, intent):
        group = intent.tag.split(":", 1)[1]
        return (intent.exchange, intent.symbol, group)

    def register_buy(self, order_id, intent):
        with self._lock:
            self.buy_registry[str(order_id)] = self._key(intent)

    def register_gtt_buy(self, gtt_id, intent):
        """Register a GTT BUY order; child order will be mapped when triggered."""
        # Ensure GTT ID is always a string for consistent lookups
        gtt_id = str(gtt_id)
        with self._lock:
            self.gtt_registry[gtt_id] = self._key(intent)

    def _apply_credit(self, order_id: str, filled_qty: int, source: str):
        """Apply BUY fill credit once per order_id and release queued SELLs.

        Returns:
            list: released SELL intents.
        """
        oid = str(order_id)
        qty = int(filled_qty or 0)
        if qty <= 0:
            return []

        released = []
        with self._lock:
            if oid in self._credited_order_ids:
                print(f"[LINKER] Duplicate credit ignored: order_id={oid} source={source}")
                return []

            key = self.buy_registry.get(oid)
            if not key:
                # If we can't map this order_id yet, don't mark it credited
                return []

            self._credited_order_ids.add(oid)
            self.buy_credits[key] += qty
            print(f"[LINKER] Credited {qty} to key {key} (source={source}), total credits={self.buy_credits[key]}")

            q = self.sell_queues[key]
            while q and self.buy_credits[key] >= q[0].qty:
                sell = q.popleft()
                self.buy_credits[key] -= sell.qty
                released.append(sell)
                print(f"[LINKER] Released SELL: {sell.symbol} qty={sell.qty}, remaining credits={self.buy_credits[key]}")

        return released

    def on_buy_fill(self, order_id, filled_qty):
        released = self._apply_credit(order_id, filled_qty, source="ws")
        if released and self._release_cb:
            print(f"[LINKER] Calling release callback with {len(released)} SELLs")
            self._release_cb(released)

    def bind_gtt_child(self, gtt_id: str, child_order_id: str):
        """Map GTT child order to the same key as parent GTT BUY."""
        # Ensure both IDs are strings
        gtt_id = str(gtt_id)
        child_order_id = str(child_order_id)
        
        with self._lock:
            key = self.gtt_registry.get(gtt_id)
            if key:
                self.buy_registry[child_order_id] = key
                print(f"[LINKER] Mapped child {child_order_id} to key {key}")
            else:
                print(f"[LINKER] ⚠️  GTT {gtt_id} not found in gtt_registry. Available: {list(self.gtt_registry.keys())}")

    def credit_by_order_id(self, order_id: str, qty: int):
        """Manually add credit by known buy order_id (e.g., from GTT child order events)."""
        released = self._apply_credit(order_id, qty, source="poller")
        if released and self._release_cb:
            self._release_cb(released)

    def queue_sell(self, intent):
        released = []
        key = self._key(intent)
        with self._lock:
            q = self.sell_queues[key]
            q.append(intent)
            print(f"[LINKER] Queued SELL: {intent.symbol} qty={intent.qty} tag={intent.tag}")

            # Immediately check if existing credits can release SELLs
            while q and self.buy_credits[key] >= q[0].qty:
                sell = q.popleft()
                self.buy_credits[key] -= sell.qty
                released.append(sell)
                print(f"[LINKER] Released SELL (on queue): {sell.symbol} qty={sell.qty}, remaining credits={self.buy_credits[key]}")

        if released and self._release_cb:
            print(f"[LINKER] Calling release callback with {len(released)} SELLs (from queue_sell)")
            self._release_cb(released)

    def snapshot(self):
        def _k(k):
            # JSON-safe key representation
            if isinstance(k, tuple):
                return "|".join(map(str, k))
            return str(k)
        return {
            # dicts keyed by tuples won't render in st.json; stringify keys
            "credits": {_k(k): v for k, v in self.buy_credits.items()},
            "queues": {_k(k): len(v) for k, v in self.sell_queues.items()},
            "buy_registry": self.buy_registry,
            "gtt_registry": self.gtt_registry,
            "instance_id": hex(id(self)),
            "credited_order_ids": len(self._credited_order_ids),
        }
