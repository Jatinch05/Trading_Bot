# services/ws/gtt_watcher.py
# Robust GTT BUY watcher:
# - Polls pending GTT trigger_ids
# - Extracts child order_id on trigger
# - Binds child → same group (via linker.bind_order_to_trigger)
# - Credits BUY quantity immediately (WS may NOT emit fill updates for GTT child)
# - Removes resolved or dead triggers

from __future__ import annotations
from typing import Optional, Dict, Set
import threading
import time

from . import linker as ws_linker


_STATE = {
    "running": False,
    "thread": None,          # type: Optional[threading.Thread]
    "kite": None,            # KiteConnect
    "interval": 2.0,         # seconds
    "pending": set(),        # type: Set[str]  # trigger_ids to poll
    "resolved": {},          # type: Dict[str, str]  # trigger_id -> order_id
    "lock": threading.RLock(),
    "stop_evt": threading.Event(),
}


def is_running() -> bool:
    return bool(_STATE["running"])


def start(kite, interval: float = 2.0) -> None:
    with _STATE["lock"]:
        if _STATE["running"]:
            return
        _STATE["kite"] = kite
        _STATE["interval"] = max(1.0, float(interval))
        _STATE["stop_evt"].clear()
        t = threading.Thread(target=_loop, name="GTTWatcher", daemon=True)
        _STATE["thread"] = t
        _STATE["running"] = True
        t.start()


def stop() -> None:
    with _STATE["lock"]:
        if not _STATE["running"]:
            return
        _STATE["stop_evt"].set()

    th = _STATE["thread"]
    if th:
        th.join(timeout=3.0)

    with _STATE["lock"]:
        _STATE["running"] = False
        _STATE["thread"] = None
        _STATE["kite"] = None
        _STATE["pending"].clear()


def add_trigger(trigger_id: int | str) -> None:
    if trigger_id is None:
        return
    with _STATE["lock"]:
        _STATE["pending"].add(str(trigger_id))


def snapshot() -> dict:
    with _STATE["lock"]:
        return {
            "running": _STATE["running"],
            "pending": list(_STATE["pending"]),
            "resolved": dict(_STATE["resolved"]),
            "interval": _STATE["interval"],
        }


# --- GTT fetch helper ---
def _fetch_gtt(kite, trig_id: str) -> Optional[dict]:
    try:
        return kite.get_gtt(trig_id)
    except AttributeError:
        try:
            return kite.gtt(trig_id)
        except Exception:
            return None
    except Exception:
        return None


# --- GTT watcher loop ---
def _loop():
    while True:
        if _STATE["stop_evt"].wait(timeout=_STATE["interval"]):
            return

        with _STATE["lock"]:
            kite = _STATE["kite"]
            pend = list(_STATE["pending"])

        if not kite or not pend:
            continue

        for trig_id in pend:
            data = _fetch_gtt(kite, trig_id)
            if not isinstance(data, dict):
                continue

            status = str(data.get("status") or "").lower()
            orders = data.get("orders") or []

            # Extract child order_id + filled qty from all legs
            child_order_id = None
            filled_total = 0

            for leg in orders:
                res = (leg or {}).get("result") or {}
                ord_res = res.get("order_result") or {}

                oid = ord_res.get("order_id")
                fqty = ord_res.get("filled_quantity") or 0

                if oid:
                    child_order_id = str(oid)
                filled_total += int(fqty)

            # Case 1: Triggered → child order exists
            if status == "triggered" and child_order_id:
                ws_linker.bind_order_to_trigger(child_order_id, trig_id)

                # Credit immediately (WS may not emit deltas for GTT child)
                if filled_total > 0:
                    ws_linker.credit_by_order_id(child_order_id, filled_total)

                with _STATE["lock"]:
                    _STATE["resolved"][trig_id] = child_order_id
                    _STATE["pending"].discard(trig_id)
                continue

            # Case 2: Completed or Rejected → remove trigger
            if status in ("completed", "cancelled", "rejected", "disabled", "deleted"):
                with _STATE["lock"]:
                    _STATE["pending"].discard(trig_id)
                continue
