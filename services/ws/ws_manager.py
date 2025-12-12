# services/ws/ws_manager.py — KiteTicker wrapper with order updates → filled deltas
# Binds order_id to GTT trigger_id when present, credits by order_id/parent_id.
from __future__ import annotations

from typing import Callable, Dict, Optional, List
import threading
from collections import deque
from datetime import datetime

try:
    from kiteconnect import KiteTicker
except Exception:  # pragma: no cover
    KiteTicker = None  # type: ignore

_STATE = {
    "running": False,
    "kt": None,
    "threaded": True,
    "api_key": None,
    "access_token": None,
    "on_filled_delta": None,    # Callable[[str, int], None]
    "filled_seen": {},          # key (order_id or parent_id) -> last filled
    "events": deque(maxlen=300),
    "lock": threading.RLock(),
}

def is_running() -> bool:
    return bool(_STATE["running"])

def start(api_key: str, access_token: str, on_filled_delta: Callable[[str, int], None]) -> None:
    with _STATE["lock"]:
        if _STATE["running"]:
            return
        if KiteTicker is None:
            raise RuntimeError("kiteconnect.KiteTicker not available")

        _STATE["api_key"] = api_key
        _STATE["access_token"] = access_token
        _STATE["on_filled_delta"] = on_filled_delta
        _STATE["filled_seen"] = {}
        _STATE["events"].clear()

        kt = KiteTicker(api_key, access_token)
        _STATE["kt"] = kt

        def _push(ev: dict):
            _STATE["events"].appendleft(ev)

        def _on_connect(ws, resp):
            _push({"ts": ts(), "event": "connect", "order_id": "", "status": "", "filled": None, "delta": None, "symbol": "", "raw": None})

        def _on_close(ws, code, reason):
            _push({"ts": ts(), "event": "close", "order_id": "", "status": f"{code}/{reason}", "filled": None, "delta": None, "symbol": "", "raw": None})

        def _on_error(ws, code, reason):
            _push({"ts": ts(), "event": "error", "order_id": "", "status": f"{code}/{reason}", "filled": None, "delta": None, "symbol": "", "raw": None})

        def _get_prev(key: str) -> int:
            return int(_STATE["filled_seen"].get(key, 0))

        def _set_seen(key: str, filled: int) -> None:
            _STATE["filled_seen"][key] = int(filled)

        def _on_order_update(ws, data):
            try:
                order_id  = str(data.get("order_id") or "")
                parent_id = str(data.get("parent_order_id") or data.get("parent_id") or "")
                trig_id   = str(data.get("gtt_trigger_id") or data.get("parent_trigger_id") or data.get("trigger_id") or "")
                filled    = int(data.get("filled_quantity") or 0)
                status    = str(data.get("status") or "")
                symbol    = str(data.get("tradingsymbol") or "")

                # If a trigger id is present, bind this order_id to the saved group
                if trig_id and order_id:
                    from . import linker as _linker
                    _linker.bind_order_to_trigger(order_id, trig_id)

                # --- Compute deltas with separate tracking for order_id and parent_id ---
                prev_order = _get_prev(order_id) if order_id else 0
                prev_parent = _get_prev(parent_id) if parent_id else 0

                # Update seen counts immediately to reflect latest snapshot
                if order_id:
                    _set_seen(order_id, filled)
                if parent_id:
                    _set_seen(parent_id, filled)

                delta_order = max(0, filled - prev_order) if order_id else 0
                delta_parent = max(0, filled - prev_parent) if (parent_id and not order_id) else 0  # prefer order_id when present

                # Choose exactly one key to credit to avoid double-crediting:
                used_key = None
                used_delta = 0
                if order_id and delta_order > 0:
                    used_key = order_id
                    used_delta = delta_order
                elif (not order_id) and parent_id and delta_parent > 0:
                    used_key = parent_id
                    used_delta = delta_parent

                _push({
                    "ts": ts(), "event": "order_update",
                    "order_id": order_id, "parent_id": parent_id,
                    "status": status, "filled": filled,
                    "delta": used_delta, "symbol": symbol,
                    "gtt_trigger_id": trig_id, "raw": data,
                })

                if used_key and used_delta > 0:
                    cb = _STATE["on_filled_delta"]
                    if cb:
                        cb(used_key, used_delta)

            except Exception as e:
                _push({
                    "ts": ts(), "event": "order_update_error",
                    "order_id": str(data.get("order_id") or ""),
                    "status": f"exc:{e}", "filled": None, "delta": None,
                    "symbol": str(data.get("tradingsymbol") or ""), "raw": data,
                })

        kt.on_connect = _on_connect
        kt.on_close = _on_close
        kt.on_error = _on_error
        kt.on_order_update = _on_order_update

        _STATE["running"] = True
        kt.connect(threaded=_STATE["threaded"])

def stop() -> None:
    with _STATE["lock"]:
        if not _STATE["running"]:
            return
        try:
            if _STATE["kt"]:
                _STATE["kt"].close()
        finally:
            _STATE["kt"] = None
            _STATE["running"] = False
            _STATE["on_filled_delta"] = None
            _STATE["filled_seen"] = {}

def events(limit: int = 100) -> List[dict]:
    with _STATE["lock"]:
        out = list(_STATE["events"])
    return out[:limit]

def clear_events() -> None:
    with _STATE["lock"]:
        _STATE["events"].clear()

def ts() -> str:
    return datetime.now().isoformat(timespec="seconds")
