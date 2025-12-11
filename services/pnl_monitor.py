# services/pnl_monitor.py — positions polling + net P&L aggregation + kill-switch arming/trip

from __future__ import annotations
from typing import Optional, Dict, Any
from threading import Thread, Lock, Event
import time

class _State:
    def __init__(self):
        self.lock = Lock()
        self.running = False
        self.thread: Optional[Thread] = None
        self.stop_ev = Event()
        self.snapshot: Dict[str, Any] = {
            "rows": [],              # list of dicts: exchange, symbol, product, net_qty, avg_price, last_price, pnl
            "net_pnl": 0.0,
            "net_profit": 0.0,
            "net_loss": 0.0,
            "error": None,
            "tripped": False,        # set True when thresholds breached
            "armed": False,          # kill switch armed (thresholds enabled)
            "tp": None,              # take profit ₹
            "sl": None,              # stop loss ₹
        }
        self.kite = None

_STATE = _State()

def get_snapshot() -> Dict[str, Any]:
    with _STATE.lock:
        return dict(_STATE.snapshot)

def is_running() -> bool:
    with _STATE.lock:
        return _STATE.running

def arm_kill_switch(enabled: bool, take_profit: Optional[float], stop_loss: Optional[float]) -> None:
    with _STATE.lock:
        _STATE.snapshot["armed"] = bool(enabled)
        _STATE.snapshot["tp"] = float(take_profit) if (take_profit is not None and take_profit != "") else None
        _STATE.snapshot["sl"] = float(stop_loss) if (stop_loss is not None and stop_loss != "") else None
        _STATE.snapshot["tripped"] = False  # reset when re-arming

def start(kite, interval_sec: float = 2.0) -> None:
    with _STATE.lock:
        if _STATE.running:
            return
        _STATE.running = True
        _STATE.kite = kite
        _STATE.stop_ev.clear()

    def _loop():
        while True:
            if _STATE.stop_ev.wait(timeout=0):
                break
            try:
                pos = _STATE.kite.positions() or {}
                rows = []
                net_pnl = net_profit = net_loss = 0.0
                for p in (pos.get("net") or []):
                    if str(p.get("product") or "").upper() != "NRML":
                        continue
                    qty = int(p.get("quantity") or p.get("net_quantity") or 0)
                    if qty == 0:
                        continue
                    avg = float(p.get("average_price") or 0.0)
                    ltp = float(p.get("last_price") or 0.0)
                    pnl = float(p.get("pnl") if p.get("pnl") is not None else (ltp - avg) * qty)
                    rows.append({
                        "exchange": str(p.get("exchange") or "").upper(),
                        "symbol": str(p.get("tradingsymbol") or "").upper(),
                        "product": "NRML",
                        "net_qty": qty,
                        "avg_price": avg,
                        "last_price": ltp,
                        "pnl": pnl,
                    })
                    net_pnl += pnl
                    if pnl >= 0:
                        net_profit += pnl
                    else:
                        net_loss += -pnl

                with _STATE.lock:
                    _STATE.snapshot.update({
                        "rows": rows, "net_pnl": net_pnl,
                        "net_profit": net_profit, "net_loss": net_loss,
                        "error": None,
                    })
                    # kill switch check
                    if _STATE.snapshot.get("armed"):
                        tp = _STATE.snapshot.get("tp")
                        sl = _STATE.snapshot.get("sl")
                        if tp is not None and net_pnl >= tp:
                            _STATE.snapshot["tripped"] = True
                        if sl is not None and net_pnl <= -abs(sl):
                            _STATE.snapshot["tripped"] = True
            except Exception as e:
                with _STATE.lock:
                    _STATE.snapshot["error"] = str(e)

            waited = 0.0
            while waited < interval_sec and not _STATE.stop_ev.is_set():
                time.sleep(0.2); waited += 0.2

        with _STATE.lock:
            _STATE.running = False

    t = Thread(target=_loop, name="PNLMonitor", daemon=True)
    with _STATE.lock:
        _STATE.thread = t
    t.start()

def stop() -> None:
    with _STATE.lock:
        if not _STATE.running:
            return
        _STATE.stop_ev.set()
