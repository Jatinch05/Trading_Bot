"""Quick sanity check for OrderLinker credit dedup.

Run:
  python test_linker_dedup_quick.py

This does not require kiteconnect; it simulates WS + poller both reporting COMPLETE
for the same BUY order_id.
"""

from dataclasses import dataclass

from services.ws.linker import OrderLinker


@dataclass
class DummyIntent:
    exchange: str
    symbol: str
    qty: int
    txn_type: str
    tag: str


def main():
    linker = OrderLinker()

    placed = []

    def cb(sells):
        # Record what got released
        placed.extend([(s.exchange, s.symbol, s.qty, s.tag) for s in sells])

    linker.set_release_callback(cb)

    buy = DummyIntent(exchange="BFO", symbol="SENSEX25D1884800CE", qty=60, txn_type="BUY", tag="link:1")
    sell1 = DummyIntent(exchange="BFO", symbol="SENSEX25D1884800CE", qty=40, txn_type="SELL", tag="link:1")
    sell2 = DummyIntent(exchange="BFO", symbol="SENSEX25D1884800CE", qty=20, txn_type="SELL", tag="link:1")

    # simulate mapping
    linker.register_buy("OID-1", buy)

    # queue sells
    linker.queue_sell(sell1)
    linker.queue_sell(sell2)

    # both sources credit same order
    linker.on_buy_fill("OID-1", 60)          # WS
    linker.credit_by_order_id("OID-1", 60)   # poller

    snap = linker.snapshot()
    print("placed:", placed)
    print("credits:", snap["credits"])
    assert sum(x[2] for x in placed) == 60, "Should only release 60 total"


if __name__ == "__main__":
    main()
