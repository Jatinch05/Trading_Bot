from services.orders.placement import place_orders
from services.ws import linker as L

def test_dryrun_generates_order_ids_and_registers_buys(oi):
    L.set_placers(lambda p: "S1", lambda p: "G1")
    L.start()
    rows = place_orders([oi(txn_type="BUY", tag="link:g9")], kite=None, live=False)
    assert rows.iloc[0]["order_id"] is not None
