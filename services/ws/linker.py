# services/ws/linker.py
from collections import defaultdict, deque

class OrderLinker:
    def __init__(self):
        self.buy_credits = defaultdict(int)     # key -> filled qty
        self.sell_queues = defaultdict(deque)   # key -> deque[OrderIntent]
        self.buy_registry = {}                  # order_id -> key
        self._release_cb = None

    def set_release_callback(self, cb):
        self._release_cb = cb

    def _key(self, intent):
        group = intent.tag.split(":", 1)[1]
        return (intent.exchange, intent.symbol, group)

    def register_buy(self, order_id, intent):
        self.buy_registry[order_id] = self._key(intent)

    def on_buy_fill(self, order_id, filled_qty):
        if order_id not in self.buy_registry:
            return

        key = self.buy_registry[order_id]
        self.buy_credits[key] += filled_qty

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
        }
