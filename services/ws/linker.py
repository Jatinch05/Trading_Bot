# services/ws/linker.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4
import time

# ---------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------

Key = Tuple[str, str, str]  # (EXCHANGE, SYMBOL, group)

PlaceRegularSell = Callable[[Dict[str, Any]], str]
PlaceGttSell = Callable[[Dict[str, Any]], str]

@dataclass
class DeferredSell:
    exchange: str
    symbol: str
    qty: int
    kind: str  # "regular" | "gtt-single" | "gtt-oco"
    meta: Dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # avoid leaking heavy objects in meta
        d["meta"] = dict(self.meta or {})
        return d


# ---------------------------------------------------------------------
# State
# ---------------------------------------------------------------------

_S = {
    "lock": threading.RLock(),
    "running": False,

    # Credits by (EX, SYM, group)
    "credits": {},  # Dict[Key, int]

    # Queues by key
    "queues": {},   # Dict[Key, List[DeferredSell]]

    # BUY registration maps
    "buy_registry": {},   # order_id -> Key
    "gtt_triggers": {},   # trigger_id -> Key

    # Placers (injected)
    "place_regular_sell": None,  # type: Optional[PlaceRegularSell]
    "place_gtt_sell": None,      # type: Optional[PlaceGttSell]

    # Log
    "logs": [],    # List[Dict]
    "max_logs": 2000,
}


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------

def _log(event: str, **kv: Any) -> None:
    with _S["lock"]:
        _S["logs"].append({"t": time.time(), "event": event, **kv})
        if len(_S["logs"]) > _S["max_logs"]:
            del _S["logs"][: len(_S["logs"]) - _S["max_logs"]]

def _norm_exchange(ex: str) -> str:
    return (ex or "").strip().upper()

def _norm_symbol(sym: str) -> str:
    return (sym or "").strip().upper()

def _norm_group(tag_or_group: str) -> str:
    s = (tag_or_group or "").strip()
    if s.lower().startswith("link:"):
        s = s.split(":", 1)[1]
    return s.strip()

def _key(exchange: str, symbol: str, group: str) -> Key:
    return (_norm_exchange(exchange), _norm_symbol(symbol), _norm_group(group))

def _ensure_group(k: Key) -> None:
    with _S["lock"]:
        _S["credits"].setdefault(k, 0)
        _S["queues"].setdefault(k, [])

def _require_placer() -> Tuple[PlaceRegularSell, PlaceGttSell]:
    pr = _S.get("place_regular_sell")
    pg = _S.get("place_gtt_sell")
    if not callable(pr) or not callable(pg):
        raise RuntimeError("Placers not configured. Call set_placers() before live use.")
    return pr, pg


# ---------------------------------------------------------------------
# Public API (configuration)
# ---------------------------------------------------------------------

def set_placers(place_regular_sell: PlaceRegularSell, place_gtt_sell: PlaceGttSell) -> None:
    """Inject sell placers. Each must return an order_id (str) or raise on failure."""
    with _S["lock"]:
        _S["place_regular_sell"] = place_regular_sell
        _S["place_gtt_sell"] = place_gtt_sell
    _log("placers_set")

def start() -> None:
    with _S["lock"]:
        _S["running"] = True
    _log("linker_started")

def stop() -> None:
    with _S["lock"]:
        _S["running"] = False
    _log("linker_stopped")


# ---------------------------------------------------------------------
# Public API (SELL queueing)
# ---------------------------------------------------------------------

def defer_sells(intents: List[Dict[str, Any]]) -> List[str]:
    """
    Enqueue SELL intents. Returns list of synthetic queue IDs.
    Each intent requires: exchange, symbol|tradingsymbol, quantity, tag (link:n), kind.
      kind: "regular" | "gtt-single" | "gtt-oco"
    Optional meta: price, trigger values, etc.
    """
    ids: List[str] = []
    for it in intents:
        tag = it.get("tag", "")
        if not tag or not tag.lower().startswith("link:"):
            _log("sell_discarded_no_group", intent=it)
            continue

        ex = it.get("exchange") or it.get("ex") or ""
        sym = it.get("symbol") or it.get("tradingsymbol") or ""
        qty = int(it.get("quantity") or it.get("qty") or 0)
        kind = (it.get("kind") or "regular").lower()

        if qty <= 0 or not ex or not sym:
            _log("sell_discarded_invalid", intent=it)
            continue

        k = _key(ex, sym, tag)
        _ensure_group(k)

        qid = f"QS-{uuid4().hex[:8]}"
        sell = DeferredSell(
            exchange=k[0],
            symbol=k[1],
            qty=qty,
            kind=kind,
            meta={"qid": qid, **(it.get("meta") or {})},
        )
        with _S["lock"]:
            _S["queues"][k].append(sell)

        ids.append(qid)
        _log("sell_enqueued", key=str(k), qid=qid, qty=qty, kind=kind)
    return ids


# ---------------------------------------------------------------------
# Public API (BUY registration & crediting)
# ---------------------------------------------------------------------

def register_buy_order(order_id: str, exchange: str, symbol: str, tag_or_group: str) -> None:
    k = _key(exchange, symbol, tag_or_group)
    _ensure_group(k)
    with _S["lock"]:
        _S["buy_registry"][order_id] = k
    _log("buy_registered", order_id=order_id, key=str(k))

def register_gtt_trigger(trigger_id: str, exchange: str, symbol: str, tag_or_group: str) -> None:
    k = _key(exchange, symbol, tag_or_group)
    _ensure_group(k)
    with _S["lock"]:
        _S["gtt_triggers"][trigger_id] = k
    _log("gtt_trigger_registered", trigger_id=trigger_id, key=str(k))

def bind_order_to_trigger(order_id: str, trigger_id: str) -> None:
    with _S["lock"]:
        k = _S["gtt_triggers"].get(trigger_id)
        if not k:
            _log("bind_missing_trigger", trigger_id=trigger_id)
            return
        _S["buy_registry"][order_id] = k
        del _S["gtt_triggers"][trigger_id]
    _log("gtt_bound_to_order", trigger_id=trigger_id, order_id=order_id, key=str(k))

def credit_by_order_id(order_id: str, delta_qty: int) -> None:
    """Called from WS feed on partial/total fills."""
    if delta_qty <= 0:
        return
    with _S["lock"]:
        k = _S["buy_registry"].get(order_id)
    if not k:
        _log("credit_unknown_order", order_id=order_id, delta=delta_qty)
        return
    apply_credit(k, delta_qty)

def apply_credit(key_or_tuple: Any, delta_qty: int) -> None:
    """Increase credit for a key and attempt releasing queued SELLs."""
    if delta_qty <= 0:
        return
    if isinstance(key_or_tuple, tuple):
        k = key_or_tuple  # assume already normalized Key
    else:
        # string form like "('NFO','NIFTY24DECFUT','3')" or "NFO,NIFTY,3"
        s = str(key_or_tuple).strip("() ")
        parts = [p.strip(" '") for p in s.replace("'", "").split(",")]
        if len(parts) == 3:
            k = (_norm_exchange(parts[0]), _norm_symbol(parts[1]), _norm_group(parts[2]))
        else:
            _log("apply_credit_bad_key", key=str(key_or_tuple))
            return

    _ensure_group(k)
    with _S["lock"]:
        _S["credits"][k] += int(delta_qty)
        credit_now = _S["credits"][k]
    _log("credit_applied", key=str(k), delta=delta_qty, credit=credit_now)

    _try_release(k)


# ---------------------------------------------------------------------
# Release engine
# ---------------------------------------------------------------------

def _try_release(k: Key, dry_preview: bool = False, persist_preview: bool = False) -> None:
    """
    Attempt to release queued SELLs for key k while credit allows.
    When dry_preview=True, do not call placers; only simulate releases.
    If persist_preview=False, do not mutate credits or queues after preview.
    """
    _ensure_group(k)
    with _S["lock"]:
        q = _S["queues"][k]
        credit = _S["credits"][k]
        running = _S["running"]

    i = 0
    simulated_changes: List[Tuple[int, DeferredSell]] = []
    placed: List[Dict[str, Any]] = []

    while i < len(q) and credit >= q[i].qty:
        sell = q[i]

        if dry_preview:
            simulated_changes.append((i, sell))
            credit -= sell.qty
            i += 1
            continue

        if not running:
            # linker not running → do nothing live; leave in queue
            _log("release_skipped_not_running", key=str(k))
            break

        try:
            pr, pg = _require_placer()

            if sell.kind == "regular":
                order_id = pr(_build_regular_payload(k, sell))
            elif sell.kind in ("gtt-single", "gtt-oco"):
                order_id = pg(_build_gtt_payload(k, sell))
            else:
                raise ValueError(f"Unknown SELL kind: {sell.kind}")

            # Placement OK → consume credit and remove from queue
            with _S["lock"]:
                credit -= sell.qty
                _S["credits"][k] = credit
                q.pop(i)
            placed.append({"order_id": order_id, "sell": sell.to_dict()})
            _log("sell_placed", key=str(k), order_id=order_id, qty=sell.qty, kind=sell.kind)

        except Exception as e:
            # Robust: refund credit, reinsert at the same position, then skip once
            with _S["lock"]:
                _S["credits"][k] = credit + sell.qty  # refund
                q.insert(i, sell)  # reinstate
            _log("sell_place_error", key=str(k), err=str(e), reinqueued=True)
            i += 1  # avoid tight retry loop

    # Persist or discard preview effects
    if dry_preview:
        if persist_preview:
            with _S["lock"]:
                # consume the previewed sells and update credits
                for idx, s in reversed(simulated_changes):
                    if idx < len(_S["queues"][k]) and _S["queues"][k][idx] is s:
                        _S["queues"][k].pop(idx)
                _S["credits"][k] = credit
            _log("preview_persisted", key=str(k), consumed=len(simulated_changes), credit=credit)
        else:
            _log("preview_only", key=str(k), would_consume=len(simulated_changes), resulting_credit=credit)

    if placed:
        _log("release_summary", key=str(k), placed=len(placed))


def _build_regular_payload(k: Key, sell: DeferredSell) -> Dict[str, Any]:
    # Enforce NRML; caller ensures fields like price/order_type
    payload = {
        "exchange": k[0],
        "tradingsymbol": k[1],
        "transaction_type": "SELL",
        "quantity": int(sell.qty),
        "product": "NRML",
        "order_type": sell.meta.get("order_type", "MARKET"),
    }
    if payload["order_type"] in ("LIMIT", "SL", "SL-M"):
        # Optional price/trigger_price
        if "price" in sell.meta and sell.meta["price"] is not None:
            payload["price"] = float(sell.meta["price"])
        if "trigger_price" in sell.meta and sell.meta["trigger_price"] is not None:
            payload["trigger_price"] = float(sell.meta["trigger_price"])
    # Optional tag (still group-based logic is here, not symbol fallback)
    if "tag" in sell.meta and sell.meta["tag"]:
        payload["tag"] = sell.meta["tag"]
    return payload


def _build_gtt_payload(k: Key, sell: DeferredSell) -> Dict[str, Any]:
    # Enforce NRML GTT shape; kite placer will translate as needed
    base = {
        "exchange": k[0],
        "tradingsymbol": k[1],
        "product": "NRML",
        "transaction_type": "SELL",
        "quantity": int(sell.qty),
    }
    if sell.kind == "gtt-single":
        base.update({
            "type": "single",
            "trigger_price": float(sell.meta.get("trigger_price")),
            "price": float(sell.meta.get("price")),
        })
    else:
        base.update({
            "type": "oco",
            "trigger_prices": [
                float(sell.meta.get("trigger_price_1")),
                float(sell.meta.get("trigger_price_2")),
            ],
            "prices": [
                float(sell.meta.get("price_1")),
                float(sell.meta.get("price_2")),
            ],
        })
    # Optional tag for local tracing (Kite GTT ignores it)
    if "tag" in sell.meta and sell.meta["tag"]:
        base["tag"] = sell.meta["tag"]
    return base


# ---------------------------------------------------------------------
# Public API (dry-run / simulator helpers)
# ---------------------------------------------------------------------

def available_keys() -> List[str]:
    with _S["lock"]:
        keys = set(_S["credits"].keys()) | set(_S["queues"].keys())
    return [str(k) for k in keys]

def groups_with_pending_sells() -> List[str]:
    with _S["lock"]:
        out = [str(k) for k, v in _S["queues"].items() if v]
    return out

def simulate_credit_offline_by_key(key_str: str, qty: int, persist: bool = False) -> Dict[str, Any]:
    """
    Simulate crediting a key and releasing SELLs without calling placers.
    If persist=True, the consumed SELLs and credit changes are committed.
    """
    if qty <= 0:
        return {"ok": False, "error": "qty must be > 0"}

    # Parse key string back to Key
    s = key_str.strip("() ")
    parts = [p.strip(" '") for p in s.replace("'", "").split(",")]
    if len(parts) != 3:
        return {"ok": False, "error": f"bad key: {key_str}"}
    k = (_norm_exchange(parts[0]), _norm_symbol(parts[1]), _norm_group(parts[2]))

    _ensure_group(k)
    with _S["lock"]:
        _S["credits"][k] += int(qty)
        credit_after = _S["credits"][k]

    _try_release(k, dry_preview=True, persist_preview=persist)

    with _S["lock"]:
        qlen = len(_S["queues"][k])
        credit_now = _S["credits"][k]

    return {"ok": True, "key": str(k), "credit": credit_now, "queued": qlen, "persisted": persist}


# ---------------------------------------------------------------------
# Public API (introspection)
# ---------------------------------------------------------------------

def snapshot() -> Dict[str, Any]:
    with _S["lock"]:
        credits = {str(k): v for k, v in _S["credits"].items()}
        queues = {str(k): [s.to_dict() for s in v] for k, v in _S["queues"].items()}
        buys = dict(_S["buy_registry"])
        trig = dict(_S["gtt_triggers"])
        running = bool(_S["running"])
        logs = list(_S["logs"][-200:])  # last 200
    return {
        "running": running,
        "credits": credits,
        "queues": queues,
        "buy_registry": buys,
        "gtt_triggers": trig,
        "logs_tail": logs,
    }


# ---------------------------------------------------------------------
# Convenience (unit helpers)
# ---------------------------------------------------------------------

def clear_all() -> None:
    with _S["lock"]:
        _S["credits"].clear()
        _S["queues"].clear()
        _S["buy_registry"].clear()
        _S["gtt_triggers"].clear()
        _S["logs"].clear()
    _log("cleared")


def is_running() -> bool:
    """
    Return True if linker has been started via start() and not stopped.
    Used by app.py to show linker thread status.
    """
    with _S["lock"]:
        return bool(_S["running"])