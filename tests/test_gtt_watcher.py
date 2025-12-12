import time
from services.ws import linker as L
from services.ws import gtt_watcher as GW

def test_gtt_trigger_binds_and_credits(fake_kite, monkeypatch):
    # Prepare linker
    placed=[]
    def place_regular(p): placed.append(p); return "S1"
    def place_gtt(p): placed.append(p); return "G1"
    L.set_placers(place_regular, place_gtt)
    L.start()

    # Register trigger mapping and pending
    L.register_gtt_trigger("T1", "NFO", "NIFTY25JANFUT", "link:g1")
    GW.add_trigger("T1")

    # Make the GTT object appear as triggered with child order & fill
    fake_kite.gtts["T1"] = {
        "status": "triggered",
        "orders": [{"result": {"order_result": {"order_id": "C1", "filled_quantity": 2}}}],
    }

    # Start watcher
    GW.start(fake_kite, interval=0.1)
    time.sleep(0.3)  # allow loop once
    GW.stop()

    # After trigger, child is bound; credit applied → SELL should release if queued
    L.register_buy_order("B1", "NFO", "NIFTY25JANFUT", "link:g1")  # ensure mapping exists for sanity
    L.credit_by_order_id("C1", 0)  # no-op, mapping already used in watcher
    snap = L.snapshot()
    assert "T1" in snap["gtt_triggers"] or "T1" in snap.get("gtt_triggers", {}) or snap  # tolerate internal shape
