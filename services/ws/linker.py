# services/ws/linker.py
from collections import defaultdict, deque
from typing import Optional

class OrderLinker:
    def __init__(self):
        self.buy_credits = defaultdict(int)     # key -> filled qty
        self.sell_queues = defaultdict(deque)   # key -> deque[OrderIntent]
        self.buy_registry = {}                  # order_id -> key
        self.gtt_registry = {}                  # gtt_id -> key (for pending GTT BUYs)
        self._release_cb = None

    def set_release_callback(self, cb):
        self._release_cb = cb

    def _key(self, intent):
        group = intent.tag.split(":", 1)[1]
        return (intent.exchange, intent.symbol, group)

    def register_buy(self, order_id, intent):
        self.buy_registry[order_id] = self._key(intent)

    def register_gtt_buy(self, gtt_id, intent):
        """Register a GTT BUY order; child order will be mapped when triggered."""
        # Ensure GTT ID is always a string for consistent lookups
        gtt_id = str(gtt_id)
        self.gtt_registry[gtt_id] = self._key(intent)

    def on_buy_fill(self, order_id, filled_qty):
        if order_id not in self.buy_registry:
            return

        key = self.buy_registry[order_id]
        self.buy_credits[key] += filled_qty
        print(f"[LINKER] Credited {filled_qty} to key {key}, total credits={self.buy_credits[key]}")

        released = []
        q = self.sell_queues[key]

        while q and self.buy_credits[key] >= q[0].qty:
            sell = q.popleft()
            self.buy_credits[key] -= sell.qty
            released.append(sell)
            print(f"[LINKER] Released SELL: {sell.symbol} qty={sell.qty}, remaining credits={self.buy_credits[key]}")

        if released and self._release_cb:
            print(f"[LINKER] Calling release callback with {len(released)} SELLs")
            self._release_cb(released)

    def bind_gtt_child(self, gtt_id: str, child_order_id: str):
        """Map GTT child order to the same key as parent GTT BUY."""
        # Ensure both IDs are strings
        gtt_id = str(gtt_id)
        child_order_id = str(child_order_id)
        
        key = self.gtt_registry.get(gtt_id)
        if key:
            self.buy_registry[child_order_id] = key
            print(f"[LINKER] Mapped child {child_order_id} to key {key}")
        else:
            print(f"[LINKER] ⚠️  GTT {gtt_id} not found in gtt_registry. Available: {list(self.gtt_registry.keys())}")

    def credit_by_order_id(self, order_id: str, qty: int):
        """Manually add credit by known buy order_id (e.g., from GTT child order events)."""
        key = self.buy_registry.get(order_id)
        if not key:
            return
        self.buy_credits[key] += int(qty or 0)
        # attempt release
        released = []
        q = self.sell_queues[key]
        while q and self.buy_credits[key] >= q[0].qty:
            sell = q.popleft()
            self.buy_credits[key] -= sell.qty
            released.append(sell)
        if released and self._release_cb:
            self._release_cb(released)

    def queue_sell(self, intent):
        self.sell_queues[self._key(intent)].append(intent)

    def snapshot(self):
        return {
            "credits": dict(self.buy_credits),
            "queues": {k: len(v) for k, v in self.sell_queues.items()},
            "buy_registry": self.buy_registry,
            "gtt_registry": self.gtt_registry,
        }
