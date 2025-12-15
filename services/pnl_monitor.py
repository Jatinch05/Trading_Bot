import threading
import time
from typing import Dict, Any, Callable, Optional

from services.orders.exit import build_exit_intents_from_positions
from services.orders.pipeline import execute_bundle

_snapshot = {
    "rows": [],
    "net_pnl": 0.0,
    "net_profit": 0.0,
    "net_loss": 0.0,
    "tripped": False,
    "error": None,
    "exit_results": None,
}

_thread = None
_running = False
_kite = None

_ks_enabled = False
_ks_tp = 0.0
_ks_sl = 0.0

_exit_in_progress = False
_live_mode = True
_on_exit_callback: Optional[Callable[[Any], None]] = None


def is_running():
    return _running


def start(kite, live: bool = True, on_exit_callback: Optional[Callable] = None):
    global _running, _kite, _thread, _live_mode, _on_exit_callback
    if _running:
        return
    _kite = kite
    _live_mode = bool(live)
    _on_exit_callback = on_exit_callback
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def stop():
    global _running
    _running = False


def get_snapshot() -> Dict[str, Any]:
    return dict(_snapshot)


def arm_kill_switch(enabled: bool, tp: float, sl: float):
    global _ks_enabled, _ks_tp, _ks_sl, _exit_in_progress
    _ks_enabled = enabled
    _ks_tp = float(tp or 0.0)
    _ks_sl = float(sl or 0.0)
    if not enabled:
        _snapshot["tripped"] = False
        _snapshot["exit_results"] = None
        _exit_in_progress = False


def _loop():
    global _exit_in_progress

    while _running:
        rows = []
        net_pnl = 0.0
        net_profit = 0.0
        net_loss = 0.0

        try:
            pos = _kite.positions()
            net = pos.get("net", [])
            for p in net:
                if p.get("product") != "NRML":
                    continue
                symbol = p.get("tradingsymbol")
                exchange = p.get("exchange")
                qty = int(p.get("quantity", 0))
                pnl = float(p.get("pnl", 0.0))

                rows.append({"exchange": exchange, "symbol": symbol, "qty": qty, "pnl": pnl})

                net_pnl += pnl
                if pnl >= 0:
                    net_profit += pnl
                else:
                    net_loss += pnl

            _snapshot.update({
                "rows": rows,
                "net_pnl": net_pnl,
                "net_profit": net_profit,
                "net_loss": net_loss,
                "error": None,
            })

        except Exception as e:
            _snapshot.update({
                "rows": [],
                "net_pnl": 0.0,
                "net_profit": 0.0,
                "net_loss": 0.0,
                "error": str(e),
            })
            time.sleep(1)
            continue

        if _ks_enabled and not _exit_in_progress:
            hit_tp = (_ks_tp > 0 and net_pnl >= _ks_tp)
            hit_sl = (_ks_sl > 0 and net_pnl <= -abs(_ks_sl))

            if hit_tp or hit_sl:
                _snapshot["tripped"] = True
                _exit_in_progress = True

                intents = build_exit_intents_from_positions(_kite)

                results = execute_bundle(
                    intents=intents,
                    kite=_kite,
                    live=_live_mode,
                )

                _snapshot["exit_results"] = results

                if _on_exit_callback:
                    try:
                        _on_exit_callback(results)
                    except Exception:
                        pass

        time.sleep(1.2)
