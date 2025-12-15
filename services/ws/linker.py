# services/ws/linker.py

from __future__ import annotations
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple
import time

from models import OrderIntent

Key = Tuple[str, str, str]  # (exchange, symbol, group)

_STATE = {
    "lock": threading.RLock(),
    "running": False,

    "credits": {},        # Key -> filled qty
    "queues": {},         # Key -> List[OrderIntent]

    "buy_registry": {},   # order_id -> Key
    "gtt_triggers": {},   # trigger_id -> Key

    "release_cb": None,   # Callable[[List[OrderIntent]], None]

    "logs": [],
}


def start():
    with _STATE["lock"]:
        _STATE["running"] = True


def stop():
    with _STATE["lock"]:
        _STATE["running"] = False


def set_release_callback(cb):
    _STATE["release_cb"] = cb


def _key(exchange, symbol, tag):
    group = tag.split(":", 1)[1]
    return (exchange.upper(), symbol.upper(), group)


# ------------------------------------------------------------------
# BUY registration
# ------------------------------------------------------------------

def register_buy_order(order_id, exchange, symbol, tag):
    if not tag or not tag.startswith("link:"):
        return
    k = _key(exchange, symbol, tag)
    with _STATE["lock"]:
        _STATE["buy_registry"][order_id] = k
        _STATE["credits"].setdefault(k, 0)
        _STATE["queues"].setdefault(k, [])


def register_gtt_trigger(trigger_id, exchange, symbol, tag):
    if not tag or not tag.startswith("link:"):
        return
    k = _key(exchange, symbol, tag)
    with _STATE["lock"]:
        _STATE["gtt_triggers"][str(trigger_id)] = k
        _STATE["credits"].setdefault(k, 0)
        _STATE["queues"].setdefault(k, [])


def bind_order_to_trigger(order_id, trigger_id):
    with _STATE["lock"]:
        k = _STATE["gtt_triggers"].pop(str(trigger_id), None)
        if k:
            _STATE["buy_registry"][order_id] = k


# ------------------------------------------------------------------
# CREDITING
# ------------------------------------------------------------------

def credit_by_order_id(order_id, delta_qty):
    if delta_qty <= 0:
        return
    with _STATE["lock"]:
        k = _STATE["buy_registry"].get(order_id)
        if not k:
            return
        _STATE["credits"][k] += delta_qty
    _try_release(k)


# ------------------------------------------------------------------
# SELL QUEUEING
# ------------------------------------------------------------------

def defer_sells(intents: List[OrderIntent]):
    for it in intents:
        if not it.tag or not it.tag.startswith("link:"):
            continue
        k = _key(it.exchange, it.symbol, it.tag)
        with _STATE["lock"]:
            _STATE["credits"].setdefault(k, 0)
            _STATE["queues"].setdefault(k, [])
            _STATE["queues"][k].append(it)


# ------------------------------------------------------------------
# RELEASE ENGINE
# ------------------------------------------------------------------

def _try_release(k: Key):
    if not _STATE["running"]:
        return
    cb = _STATE["release_cb"]
    if not callable(cb):
        return

    released: List[OrderIntent] = []

    with _STATE["lock"]:
        credit = _STATE["credits"][k]
        q = _STATE["queues"][k]

        i = 0
        while i < len(q):
            sell = q[i]
            if sell.qty <= credit:
                credit -= sell.qty
                released.append(sell)
                q.pop(i)
            else:
                i += 1

        _STATE["credits"][k] = credit

    if released:
        cb(released)


# ------------------------------------------------------------------
# DEBUG
# ------------------------------------------------------------------

def snapshot():
    with _STATE["lock"]:
        return {
            "running": _STATE["running"],
            "credits": {str(k): v for k, v in _STATE["credits"].items()},
            "queues": {str(k): [i.model_dump() for i in v] for k, v in _STATE["queues"].items()},
            "buy_registry": dict(_STATE["buy_registry"]),
            "gtt_triggers": dict(_STATE["gtt_triggers"]),
        }
