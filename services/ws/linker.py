# services/ws/linker.py

from collections import defaultdict, deque
import threading
import time

def make_key(exchange, symbol, link):
    return (exchange, symbol, str(link))


class OrderLinker:
    def __init__(self):
        self._lock = threading.Lock()

        # (exchange, symbol, link) -> int
        self.credits = defaultdict(int)

        # (exchange, symbol, link) -> deque[OrderIntent]
        self.queues = defaultdict(deque)

        # order_id -> (exchange, symbol, link)
        self.buy_registry = {}

        self._release_cb = None
        self._logs = deque(maxlen=200)
        self._running = True

    # =====================================================
    # Compatibility: OLD + NEW names
    # =====================================================
    def set_release_cb(self, cb):
        self._release_cb = cb
        self._log("release_cb attached")

    def set_release_callback(self, cb):
        # backward-compat alias used by app.py
        self.set_release_cb(cb)

    # =====================================================
    # SELL path (UNCHANGED behavior)
    # =====================================================
    def queue_sell(self, intent):
        key = make_key(intent.exchange, intent.symbol, intent.link)
        with self._lock:
            self.queues[key].append(intent)
            self._log(f"SELL queued {key} qty={intent.qty}")
            self._try_release_locked(key)

    def _try_release_locked(self, key):
        if not self._release_cb:
            return

        available = self.credits.get(key, 0)
        if available <= 0:
            return

        q = self.queues[key]
        released = []

        while q and available > 0:
            sell = q[0]

            if sell.qty <= available:
                available -= sell.qty
                q.popleft()
                released.append(sell)
            else:
                partial = sell.copy_with_qty(available)
                sell.qty -= available
                available = 0
                released.append(partial)

        self.credits[key] = available

        if released:
            self._log(f"Releasing {len(released)} SELL(s) for {key}")
            self._release_cb(released)

    # =====================================================
    # BUY path (FIXED)
    # =====================================================
    def register_buy_order(self, order_id, exchange, symbol, link):
        with self._lock:
            self.buy_registry[str(order_id)] = (exchange, symbol, str(link))
            self._log(f"BUY registered {order_id}")

    def credit_from_fill(self, order_id, filled_qty):
        oid = str(order_id)
        with self._lock:
            if oid not in self.buy_registry:
                return

            exchange, symbol, link = self.buy_registry[oid]
            key = make_key(exchange, symbol, link)

            self.credits[key] += int(filled_qty)
            self._log(f"BUY credit +{filled_qty} for {key}")
            self._try_release_locked(key)

    # =====================================================
    # Debug / Introspection
    # =====================================================
    def snapshot(self):
        with self._lock:
            return {
                "running": self._running,
                "credits": dict(self.credits),
                "queues": {
                    k: [i.to_dict() for i in v]
                    for k, v in self.queues.items()
                },
                "buy_registry": dict(self.buy_registry),
                "has_release_cb": bool(self._release_cb),
                "logs_tail": list(self._logs),
            }

    def _log(self, msg):
        self._logs.appendleft({
            "ts": time.strftime("%H:%M:%S"),
            "msg": msg,
        })
