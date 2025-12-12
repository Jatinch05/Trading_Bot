from services.orders.pipeline import execute_bundle
from services.ws import linker as L

def test_pipeline_registers_buys_and_defers_sells(oi, fake_kite):
    # set placers
    placed=[]
    def place_regular(p): placed.append(p); return "SREG"
    def place_gtt(p): placed.append(p); return "SGTT"
    L.set_placers(place_regular, place_gtt)
    L.start()

    intents = [
        oi(txn_type="BUY", tag="link:g1"),
        oi(txn_type="SELL", tag="link:g1"),
    ]
    res = execute_bundle(intents, kite=fake_kite, live=False, link_sells_via_ws=True)
    # Expect a DEFERRED_SELL row and a DRYRUN BUY row
    kinds = {r.get("kind") for r in res}
    assert "DEFERRED_SELL" in kinds
    assert "REGULAR" in kinds
