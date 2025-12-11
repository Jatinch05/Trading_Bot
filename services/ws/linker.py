# services/ws/linker.py — NRML-only Buy→Sell linker
# Tag-first matching via Excel tag "link:<group>", with fallback to symbol-level.
# Idempotent delta accounting + atomic reservation. Includes debug prints.
# NOTE: placement functions are injected via configure(...) to avoid circular imports.

from __future__ import annotations
from typing import Dict, Tuple, List, Iterable, Any, Optional, Callable
from threading import Thread, Lock
from collections import defaultdict
import time
import re

from kiteconnect import KiteTicker
from models import OrderIntent

# Key = (exchange, symbol, group)
Key = Tuple[str, str, Optional[str]]

# -------------------- Shared state --------------------
_lock = Lock()
_running = False
_thread: Thread | None = None
_ticker: KiteTicker | None = None
_kite_client = None  # used for SELL placements

# Injected placement functions (set by configure)
_place_regular_fn: Optional[Callable[[List[OrderIntent], Any, bool], Any]] = None
_place_gtt_single_fn: Optional[Callable[[List[OrderIntent], Any], Any]] = None
_place_gtt_oco_fn: Optional[Callable[[List[OrderIntent], Any], Any]] = None

# Filled BUY qty per (exchange, symbol, group)
_buy_filled: Dict[Key, int] = defaultdict(int)

# Deferred SELLs per (exchange, symbol, group); FIFO
_deferred: Dict[Key, List[OrderIntent]] = defaultdict(list)

# Idempotency for delta accounting: last seen cumulative filled per order_id
_last_filled_by_order: Dict[str, int] = {}

# Mapping BUY order_id -> (exchange, symbol, group) for tag-scoped accounting
_buy_oid_to_key: Dict[str, Key] = {}

# --------- Tag parsing ----------
_LINK_RE = re.compile(r"^\s*link\s*:\s*([A-Za-z0-9._\-]+)\s*$", re.IGNORECASE)

def parse_group_tag(tag: Optional[str]) -> Optional[str]:
    """Normalize 'link:<group>' → 'group' (lowercased). None if not present or not matching."""
    if not tag:
        return None
    m = _LINK_RE.match(str(tag).strip())
    if not m:
        return None
    return m.group(1).lower()

# -------------------- Public API --------------------
def configure(kite_client, place_regular_fn, place_gtt_single_fn, place_gtt_oco_fn) -> None:
    """
    Provide the Kite client and placement callables to the linker.
    This breaks the import cycle with services.orders.placement.
    """
    global _kite_client, _place_regular_fn, _place_gtt_single_fn, _place_gtt_oco_fn
    _kite_client = kite_client
    _place_regular_fn = place_regular_fn
    _place_gtt_single_fn = place_gtt_single_fn
    _place_gtt_oco_fn = place_gtt_oco_fn

def register_buy_order(order_id: str, exchange: str, symbol: str, tag: Optional[str]) -> None:
    """
    Record the BUY's group scope at placement time so WS updates credit the right pool.
    If no 'link:<group>' tag, we store group=None (fallback to symbol-level).
    """
    group = parse_group_tag(tag)
    key: Key = (str(exchange).upper(), str(symbol).upper(), group)
    with _lock:
        _buy_oid_to_key[str(order_id)] = key
        print(f"[LINKER] register_buy_order oid={order_id} key={key}")

def defer_sells(intents: Iterable[OrderIntent]) -> int:
    """Queue SELL intents by (exchange, symbol, group). Returns number queued."""
    n = 0
    with _lock:
        for it in intents:
            if it.txn_type != "SELL":
                continue
            group = parse_group_tag(getattr(it, "tag", None))
            key: Key = (it.exchange, it.symbol, group)
            _deferred[key].append(it)
            n += 1
        print(f"[LINKER] deferred queued total={n} keys={{k:len(v) for k,v in _deferred.items()}}")
    return n

def start(api_key: str, access_token: str) -> None:
    """Start KiteTicker and listen for order updates (idempotent)."""
    global _running, _thread, _ticker
    with _lock:
        if _running:
            print("[LINKER] start() ignored; already running")
            return
        _running = True

    def _run():
        global _ticker, _running
        print("[LINKER] WS thread starting")
        _ticker = KiteTicker(api_key=api_key, access_token=access_token)

        def on_connect(ws, response):
            print("[LINKER] WS connected")

        def on_close(ws, code, reason):
            print(f"[LINKER] WS closed code={code} reason={reason}")
            stop()

        def on_error(ws, code, reason):
            print(f"[LINKER] WS error code={code} reason={reason}")
            time.sleep(0.25)

        def on_order_update(ws, data: Dict[str, Any]):
            """
            Consume BUY NRML updates. We map order_id → (ex,sym,group) from register_buy_order().
            Without a mapping we fall back to (ex,sym,None) i.e., symbol-level pool.
            """
            try:
                oid   = str(data.get("order_id") or "").strip()
                txn   = str(data.get("transaction_type") or "").upper()
                prod  = str(data.get("product") or "").upper()
                sym   = str(data.get("tradingsymbol") or "").upper()
                ex    = str(data.get("exchange") or "").upper()
                filled_total = int(data.get("filled_quantity") or data.get("quantity") or 0)

                if not oid or not sym or not ex:
                    return
                if txn != "BUY" or prod != "NRML":
                    return

                with _lock:
                    key = _buy_oid_to_key.get(oid, (ex, sym, None))
                    prev  = _last_filled_by_order.get(oid, 0)
                    delta = filled_total - prev
                    pool_before = _buy_filled.get(key, 0)
                    queued = len(_deferred.get(key, []))

                    print(f"[LINKER][WS] oid={oid} filled_total={filled_total} prev={prev} delta={delta} "
                          f"key={key} pool_before={pool_before} queued_sells={queued}")

                    if delta <= 0:
                        return

                    _last_filled_by_order[oid] = filled_total
                    _buy_filled[key] = pool_before + delta

                    print(f"[LINKER][WS] pool_after={_buy_filled[key]} -> draining key={key}...")
                    _drain_locked(key)
            except Exception as e:
                print(f"[LINKER][WS] on_order_update exception: {e}")
                return

        _ticker.on_connect = on_connect
        _ticker.on_close = on_close
        _ticker.on_error = on_error
        _ticker.on_order_update = on_order_update

        try:
            _ticker.connect(threaded=False)
        except Exception as e:
            print(f"[LINKER] WS connect exception: {e}")
        finally:
            with _lock:
                _running = False
                try:
                    if _ticker:
                        _ticker.close()
                except Exception:
                    pass
                _ticker = None
            print("[LINKER] WS thread stopped")

    _thread = Thread(target=_run, name="BuySellLinker", daemon=True)
    _thread.start()

def stop() -> None:
    """Stop websocket loop; keep queues/counters intact for session."""
    global _running, _ticker, _thread
    with _lock:
        _running = False
        try:
            if _ticker:
                _ticker.close()
        except Exception:
            pass
        _ticker = None
    try:
        if _thread and _thread.is_alive():
            time.sleep(0.2)
    finally:
        _thread = None
    print("[LINKER] stopped")

def is_running() -> bool:
    with _lock:
        return _running

def clear_deferred() -> None:
    """Clear all deferred SELL queues and BUY pools (used on global exit)."""
    with _lock:
        _deferred.clear()
        _buy_filled.clear()
        print("[LINKER] deferred SELL queues + BUY pools cleared")

# -------------------- Core draining (reservation) --------------------
def _drain_locked(key: Key) -> None:
    """
    Reserve-and-place:
      - While pool >= head SELL qty, reserve and pop under the lock.
      - Place outside the lock.
      - On failure, refund and re-queue at head.
    """
    if _kite_client is None or _place_regular_fn is None or _place_gtt_single_fn is None or _place_gtt_oco_fn is None:
        print("[LINKER] drain aborted: linker not configured with client/functions")
        return

    to_place: List[OrderIntent] = []
    while True:
        pool = _buy_filled.get(key, 0)
        q = _deferred.get(key, [])
        if not q:
            break
        head = q[0]
        req = int(head.qty)
        if pool >= req:
            _buy_filled[key] = pool - req
            q.pop(0)
            to_place.append(head)
            print(f"[LINKER][RESERVE] key={key} reserved={req} pool-> {_buy_filled[key]} "
                  f"remaining_queue={len(q)}")
            continue
        print(f"[LINKER][RESERVE] key={key} insufficient pool={pool} need={req} queue_head_kept")
        break

    for it in to_place:
        ok = False
        try:
            disp_key = (it.exchange, it.symbol, parse_group_tag(getattr(it, "tag", None)))
            if (it.gtt or "").upper() == "YES":
                if (it.gtt_type or "").upper() == "OCO":
                    print(f"[LINKER][PLACE] GTT OCO SELL {disp_key} qty={it.qty}")
                    _place_gtt_oco_fn([it], kite=_kite_client)
                else:
                    print(f"[LINKER][PLACE] GTT SINGLE SELL {disp_key} qty={it.qty}")
                    _place_gtt_single_fn([it], kite=_kite_client)
            else:
                print(f"[LINKER][PLACE] REGULAR SELL {disp_key} qty={it.qty}")
                _place_regular_fn([it], kite=_kite_client, live=True)
            ok = True
        except Exception as e:
            print(f"[LINKER][PLACE] failed qty={it.qty} error={e}")
            ok = False

        if not ok:
            with _lock:
                key2 = (it.exchange, it.symbol, parse_group_tag(getattr(it, "tag", None)))
                _buy_filled[key2] = _buy_filled.get(key2, 0) + int(it.qty)
                _deferred[key2].insert(0, it)
                print(f"[LINKER][REFUND] key={key2} refunded={it.qty} pool-> {_buy_filled[key2]} "
                      f"queue_len={len(_deferred[key2])}")
