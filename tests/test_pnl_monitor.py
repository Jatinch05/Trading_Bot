import time
from services import pnl_monitor as PM
from services.orders.exit import build_exit_intents_from_positions
from services.orders.pipeline import execute_bundle

def test_kill_switch_trips_and_calls_exit(monkeypatch, fake_kite):
    fake_kite._positions = {"net":[{"exchange":"NFO","tradingsymbol":"NIFTY","product":"NRML","quantity":1,"pnl": 100.0}]}

    # Monkeypatch execute_bundle to record call
    calls = {}
    def fake_exec(intents, kite=None, live=True, link_sells_via_ws=False, **_):
        calls["called"] = True
        return [{"status":"OK"}]
    monkeypatch.setattr("services.orders.pipeline.execute_bundle", fake_exec)

    PM.start(fake_kite, live=False)
    PM.arm_kill_switch(True, tp=50.0, sl=0.0)  # TP hit
    time.sleep(1.5)
    snap = PM.get_snapshot()
    PM.stop()

    assert snap["tripped"] is True
    assert calls.get("called") is True
