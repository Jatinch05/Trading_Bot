# services/ws/linker.py
# Link-based BUY → SELL automation engine
# - Queues SELL OrderIntents
# - Tracks BUY fills via WS / GTT watcher
# - Releases SELL intents when credit is sufficient
# - NEVER talks to Kite API directly

from __future__ import annotations

import threading
from typing import Dict, List, Tuple, Callable, Optional

from models import OrderIntent

# (exchange, symbol, link_group)
Key = Tuple[str, str, str]


_STATE = {
    "lock": threading.RLock(),
    "running": False,

    # credit management
    "credits": {},          # Key -> int
    "queues": {},           # Key -> List[OrderIntent]

    # BUY tracking
    "buy_registry": {},     # order_id -> Key
    "gtt_triggers": {},     # trigger_id -> Key

    # callback
    "release_cb": None,     # Callable[[List[OrderIntent]], None]
}


# =========================================================
# Lifecycle
# =========================================================

def start() -> None:
    with _STATE["lock"]:
        _STATE["running"] = True


def stop() -> None:
    with _STATE["lock"]:
        _STATE["running"] = False


def is_running() -> bool:
    return bool(_STATE["running"])


def set_release_callback(cb: Callable[[List[OrderIntent]], None]) -> None:
    """
    Register callback to be invoked with SELL OrderIntents
    when they are ready to be released.
    """
    with _STATE["lock"]:
        _STATE["release_cb"] = cb


# =========================================================
# Helpers
# =========================================================

def _key(exchange: str, symbol: str, tag: str) -> Key:
    group = tag.split(":", 1)[1]
    return (exchange.upper(), symbol.upper(), group)


def _ensure_key(k: Key) -> None:
    _STATE["credits"].setdefault(k, 0)
    _STATE["queues"].setdefault(k, [])


# =========================================================
# BUY registration
# =========================================================

def register_buy_order(order_id: str, exchange: str, symbol: str, tag: Optional[str]) -> None:
    if not tag or not tag.startswith("link:"):
        return

    k = _key(exchange, symbol, tag)
    with _STATE["lock"]:
        _ensure_key(k)
        _STATE["buy_registry"][str(order_id)] = k


def register_gtt_trigger(trigger_id: str | int, exchange: str, symbol: str, tag: Optional[str]) -> None:
    if not tag or not tag.startswith("link:"):
        return

    k = _key(exchange, symbol, tag)
    with _STATE["lock"]:
        _ensure_key(k)
        _STATE["gtt_triggers"][str(trigger_id)] = k


def bind_order_to_trigger(order_id: str, trigger_id: str | int) -> None:
    """
    When a GTT trigger fires and child order_id is known,
    bind it to the same link group.
    """
    with _STATE["lock"]:
        k = _STATE["gtt_triggers"].pop(str(trigger_id), None)
        if k:
            _STATE["buy_registry"][str(order_id)] = k


# =========================================================
# Crediting
# =========================================================

def credit_by_order_id(order_id: str, delta_qty: int) -> None:
    if delta_qty <= 0:
        return

    with _STATE["lock"]:
        k = _STATE["buy_registry"].get(str(order_id))
        if not k:
            return

        _STATE["credits"][k] += int(delta_qty)

    _try_release(k)


# =========================================================
# SELL queueing
# =========================================================

def defer_sells(intents: List[OrderIntent]) -> None:
    """
    Queue SELL intents for later release.
    """
    for it in intents:
        if not it.tag or not it.tag.startswith("link:"):
            continue

        k = _key(it.exchange, it.symbol, it.tag)
        with _STATE["lock"]:
            _ensure_key(k)
            _STATE["queues"][k].append(it)


# =========================================================
# Release engine
# =========================================================

def _try_release(k: Key) -> None:
    if not _STATE["running"]:
        return

    cb = _STATE.get("release_cb")
    if not callable(cb):
        return

    released: List[OrderIntent] = []

    with _STATE["lock"]:
        credit = _STATE["credits"].get(k, 0)
        queue = _STATE["queues"].get(k, [])

        i = 0
        while i < len(queue):
            sell = queue[i]
            if sell.qty <= credit:
                credit -= sell.qty
                released.append(sell)
                queue.pop(i)
            else:
                i += 1

        _STATE["credits"][k] = credit

    if released:
        cb(released)


# =========================================================
# Debug / Introspection
# =========================================================

def snapshot() -> dict:
    with _STATE["lock"]:
        return {
            "running": _STATE["running"],
            "credits": {str(k): v for k, v in _STATE["credits"].items()},
            "queues": {
                str(k): [i.model_dump() for i in v]
                for k, v in _STATE["queues"].items()
            },
            "buy_registry": dict(_STATE["buy_registry"]),
            "gtt_triggers": dict(_STATE["gtt_triggers"]),
            "has_release_cb": callable(_STATE["release_cb"]),
        }
