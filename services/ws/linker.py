# services/ws/linker.py
from collections import defaultdict, deque
import threading
import json
from pathlib import Path
from typing import Optional

class OrderLinker:
    # Persistence file for state recovery across app restarts
    # Use absolute path to ensure it works regardless of working directory
    STATE_FILE = Path(__file__).parent.parent.parent / "linker_state.json"
    
    def __init__(self):
        self.buy_credits = defaultdict(int)     # key -> filled qty
        self.sell_queues = defaultdict(deque)   # key -> deque[OrderIntent]
        self.buy_queue = deque()                # deque[{intent, trigger, tolerance, queued_at}] for BUY orders waiting on price
        self.buy_registry = {}                  # order_id -> key
        self.gtt_registry = {}                  # gtt_id -> key (for pending GTT BUYs)
        self._release_cb = None
        # Shared dedup across ALL credit sources (WS + poller)
        self._credited_order_ids = set()
        # Diagnostics: how much credit was applied per key
        self._credited_qty_by_key = defaultdict(int)
        self._credited_count_by_key = defaultdict(int)
        # Protect credits/queues/releases across background threads
        self._lock = threading.Lock()
        # If WS order_update arrives before GTT watcher maps child->key,
        # stash it here and apply once mapping is available.
        self._pending_unmapped_fills = {}  # order_id -> filled_qty (max)
        # Debug: show path on init
        print(f"[LINKER] STATE_FILE configured: {self.STATE_FILE}")

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _intent_signature(self, intent):
        return (
            intent.symbol,
            intent.exchange,
            intent.qty,
            intent.order_type,
            intent.price,
            intent.trigger_price,
            intent.product,
            intent.validity,
            intent.variety,
            intent.disclosed_qty,
            intent.tag,
            intent.gtt,
            intent.gtt_type,
            getattr(intent, "gtt_trigger", None),
            getattr(intent, "gtt_limit", None),
            getattr(intent, "gtt_trigger_1", None),
            getattr(intent, "gtt_limit_1", None),
            getattr(intent, "gtt_trigger_2", None),
            getattr(intent, "gtt_limit_2", None),
        )

    def _dedupe_queues_locked(self):
        """Remove duplicate SELL intents per key, preserving order."""
        removed_total = 0
        for key, q in list(self.sell_queues.items()):
            seen = set()
            unique = deque()
            for intent in q:
                sig = self._intent_signature(intent)
                if sig in seen:
                    removed_total += 1
                    continue
                seen.add(sig)
                unique.append(intent)
            self.sell_queues[key] = unique
        if removed_total:
            print(f"[LINKER] De-duplicated SELL queues; removed {removed_total} duplicate intents")

    def _credit_lock_dir(self) -> Path:
        # .../services/ws/linker.py -> parents[2] is repo root
        d = Path(__file__).resolve().parents[2] / ".runtime" / "credited_buys"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _try_acquire_credit_inflight(self, oid: str) -> tuple[bool, str, Optional[dict]]:
        """Cross-session/process idempotency for BUY credit (two-phase).

        We acquire an `.inprogress` lock before crediting so only one session
        can credit at a time, and we only promote to `.done` after state is
        successfully saved.

        This avoids the failure mode where a permanent lock was created but the
        credit never got persisted (refresh/crash), which would make credits look
        like 0 forever.
        """
        import hashlib
        import datetime as _dt
        import os

        lock_dir = self._credit_lock_dir()
        h = hashlib.sha256(str(oid).encode("utf-8")).hexdigest()
        done = lock_dir / f"{h}.done"
        inflight = lock_dir / f"{h}.inprogress"

        done_ttl = 7 * 24 * 60 * 60   # 7d
        inflight_ttl = 5 * 60         # 5m

        def _is_stale(path, ttl):
            try:
                now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
                age = now_ts - path.stat().st_mtime
                return age > ttl
            except Exception:
                return False

        if done.exists() and not _is_stale(done, done_ttl):
            return False, "done", None
        if done.exists() and _is_stale(done, done_ttl):
            try:
                done.unlink(missing_ok=True)
            except Exception:
                return False, "done_locked", None

        if inflight.exists() and not _is_stale(inflight, inflight_ttl):
            return False, "inflight", None
        if inflight.exists() and _is_stale(inflight, inflight_ttl):
            try:
                inflight.unlink(missing_ok=True)
            except Exception:
                return False, "inflight_locked", None

        try:
            with open(inflight, "x", encoding="utf-8") as f:
                f.write(str(oid))
            return True, "acquired", {"done": done, "inflight": inflight, "os": os}
        except FileExistsError:
            return False, "inflight", None
        except Exception:
            return False, "fs_error", None

    def _promote_credit_inflight(self, ctx: dict) -> None:
        try:
            ctx["os"].replace(str(ctx["inflight"]), str(ctx["done"]))
        except Exception:
            try:
                ctx["done"].write_text("done")
            except Exception:
                pass
            try:
                ctx["inflight"].unlink(missing_ok=True)
            except Exception:
                pass

    def _release_credit_inflight(self, ctx: dict) -> None:
        try:
            ctx["inflight"].unlink(missing_ok=True)
        except Exception:
            pass

    def set_release_callback(self, cb):
        self._release_cb = cb

    def _key(self, intent):
        group = intent.tag.split(":", 1)[1]
        return (intent.exchange, intent.symbol, group)

    def register_buy(self, order_id, intent):
        with self._lock:
            self.buy_registry[str(order_id)] = self._key(intent)
        self.save_state()

    def register_gtt_buy(self, gtt_id, intent):
        """Register a GTT BUY order; child order will be mapped when triggered."""
        # Ensure GTT ID is always a string for consistent lookups
        gtt_id = str(gtt_id)
        with self._lock:
            self.gtt_registry[gtt_id] = self._key(intent)
        self.save_state()  # Persist after registration

    def _apply_credit(self, order_id: str, filled_qty: int, source: str):
        """Apply BUY fill credit once per order_id and release queued SELLs.

        Returns:
            list: released SELL intents.
        """
        oid = str(order_id)
        qty = int(filled_qty or 0)
        if qty <= 0:
            return []

        released = []
        state_changed = False
        credit_ctx = None
        with self._lock:
            if oid in self._credited_order_ids:
                print(f"[LINKER] Duplicate credit ignored: order_id={oid} source={source}")
                return []

            key = self.buy_registry.get(oid)
            if not key:
                # If we can't map this order_id yet, don't mark it credited.
                # Buffer it, so when GTT watcher binds child->key we can credit.
                prev = self._pending_unmapped_fills.get(oid, 0)
                if qty > prev:
                    self._pending_unmapped_fills[oid] = qty
                    print(f"[LINKER] Buffered unmapped fill: order_id={oid} qty={qty} source={source}")
                return []

            # Cross-session dedupe: only one session/process should credit.
            ok, reason, credit_ctx = self._try_acquire_credit_inflight(oid)
            if not ok:
                print(f"[LINKER] Cross-session credit lock ({reason}); skipping order_id={oid} source={source}")
                return []

            self._credited_order_ids.add(oid)
            self.buy_credits[key] += qty
            self._credited_qty_by_key[key] += qty
            self._credited_count_by_key[key] += 1
            state_changed = True
            print(f"[LINKER] Credited {qty} to key {key} (source={source}), total credits={self.buy_credits[key]}")

            q = self.sell_queues[key]
            while q and self.buy_credits[key] >= q[0].qty:
                sell = q.popleft()
                self.buy_credits[key] -= sell.qty
                released.append(sell)
                print(f"[LINKER] Released SELL: {sell.symbol} qty={sell.qty}, remaining credits={self.buy_credits[key]}")

        if state_changed or released:
            save_msg = self.save_state()
            # Only mark credit as done once state is persisted.
            if credit_ctx and isinstance(save_msg, str) and "✅" in save_msg:
                self._promote_credit_inflight(credit_ctx)
            elif credit_ctx:
                self._release_credit_inflight(credit_ctx)
        elif credit_ctx:
            # No state change? release lock.
            self._release_credit_inflight(credit_ctx)

        return released

    def on_buy_fill(self, order_id, filled_qty):
        released = self._apply_credit(order_id, filled_qty, source="ws")
        if released and self._release_cb:
            print(f"[LINKER] Calling release callback with {len(released)} SELLs")
            self._release_cb(released)

    def bind_gtt_child(self, gtt_id: str, child_order_id: str):
        """Map GTT child order to the same key as parent GTT BUY."""
        # Ensure both IDs are strings
        gtt_id = str(gtt_id)
        child_order_id = str(child_order_id)
        
        with self._lock:
            key = self.gtt_registry.get(gtt_id)
            if key:
                self.buy_registry[child_order_id] = key
                print(f"[LINKER] Mapped child {child_order_id} to key {key}")
            else:
                print(f"[LINKER] ⚠️  GTT {gtt_id} not found in gtt_registry. Available: {list(self.gtt_registry.keys())}")
        self.save_state()

        # If we saw a WS fill before mapping, apply it now.
        pending_qty = None
        with self._lock:
            pending_qty = self._pending_unmapped_fills.pop(child_order_id, None)
        if pending_qty:
            print(f"[LINKER] Applying buffered fill for {child_order_id}: qty={pending_qty}")
            released = self._apply_credit(child_order_id, pending_qty, source="ws_buffer")
            if released and self._release_cb:
                self._release_cb(released)

    def credit_by_order_id(self, order_id: str, qty: int):
        """Manually add credit by known buy order_id (e.g., from GTT child order events)."""
        released = self._apply_credit(order_id, qty, source="poller")
        if released and self._release_cb:
            self._release_cb(released)

    def queue_sell(self, intent):
        released = []
        key = self._key(intent)
        with self._lock:
            q = self.sell_queues[key]
            # Deduplicate identical SELL intents for the same key to avoid double placement
            sig = self._intent_signature(intent)
            existing_sigs = {self._intent_signature(s) for s in q}

            if sig in existing_sigs:
                print(
                    f"[LINKER] ⚠️ Duplicate SELL skipped: {intent.symbol} qty={intent.qty} tag={intent.tag}"
                )
            else:
                q.append(intent)
                print(f"[LINKER] Queued SELL: {intent.symbol} qty={intent.qty} tag={intent.tag}")

            # Immediately check if existing credits can release SELLs
            while q and self.buy_credits[key] >= q[0].qty:
                sell = q.popleft()
                self.buy_credits[key] -= sell.qty
                released.append(sell)
                print(f"[LINKER] Released SELL (on queue): {sell.symbol} qty={sell.qty}, remaining credits={self.buy_credits[key]}")

        self.save_state()  # Persist after queuing
        
        if released and self._release_cb:
            print(f"[LINKER] Calling release callback with {len(released)} SELLs (from queue_sell)")
            self._release_cb(released)

    def queue_buy(self, intent, trigger: float, tolerance: float):
        """Queue a BUY order to be placed when LTP hits trigger ± tolerance."""
        import datetime as _dt
        
        with self._lock:
            entry = {
                "intent": intent,
                "trigger": float(trigger),
                "tolerance": float(tolerance),
                "queued_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
            self.buy_queue.append(entry)
            print(
                f"[LINKER] Queued BUY: {intent.symbol} qty={intent.qty} "
                f"trigger={trigger} ±{tolerance}"
            )
        self.save_state()

    def check_buy_triggers(self, prices_dict: dict) -> list:
        """Check if any queued BUYs should be placed based on current prices.
        
        Args:
            prices_dict: {symbol: ltp_float} or similar structure
        
        Returns:
            list of OrderIntent objects ready to place
        """
        ready_to_place = []
        
        with self._lock:
            remaining_queue = deque()
            
            for entry in self.buy_queue:
                intent = entry["intent"]
                trigger = entry["trigger"]
                tolerance = entry["tolerance"]
                symbol = intent.symbol
                
                # Get LTP for this symbol
                ltp = None
                if isinstance(prices_dict, dict):
                    ltp = prices_dict.get(symbol)
                
                if ltp is None:
                    # No price data yet; keep in queue
                    remaining_queue.append(entry)
                    continue
                
                # Check if LTP is within trigger ± tolerance
                lower_bound = trigger - tolerance
                upper_bound = trigger + tolerance
                
                if lower_bound <= ltp <= upper_bound:
                    # Price hit! Remove from queue and add to ready list
                    ready_to_place.append(intent)
                    print(
                        f"[LINKER] BUY trigger hit: {symbol} qty={intent.qty} "
                        f"trigger={trigger} ±{tolerance}, LTP={ltp:.2f}"
                    )
                else:
                    # Still waiting; keep in queue
                    remaining_queue.append(entry)
            
            self.buy_queue = remaining_queue
        
        if ready_to_place:
            self.save_state()
        
        return ready_to_place

    def snapshot(self):
        def _k(k):
            # JSON-safe key representation
            if isinstance(k, tuple):
                return "|".join(map(str, k))
            return str(k)
        
        # Check which state files exist
        old_locations = [
            Path("linker_state.json"),
            Path.cwd() / "linker_state.json",
            Path(__file__).parent / "linker_state.json",
            Path(__file__).parent.parent / "linker_state.json",
        ]
        
        file_status = {
            "new_path": {
                "path": str(self.STATE_FILE),
                "exists": self.STATE_FILE.exists(),
            },
            "old_paths": [
                {"path": str(p.absolute()), "exists": p.exists()}
                for p in old_locations
            ]
        }
        
        return {
            # dicts keyed by tuples won't render in st.json; stringify keys
            "credits": {_k(k): v for k, v in self.buy_credits.items()},
            "queues": {_k(k): len(v) for k, v in self.sell_queues.items()},
            "buy_queue_count": len(self.buy_queue),
            "buy_registry": self.buy_registry,
            "gtt_registry": self.gtt_registry,
            "instance_id": hex(id(self)),
            "credited_order_ids": len(self._credited_order_ids),
            "credited_qty_by_key": {_k(k): v for k, v in self._credited_qty_by_key.items()},
            "credited_count_by_key": {_k(k): v for k, v in self._credited_count_by_key.items()},
            "credited_order_ids_sample": sorted(list(self._credited_order_ids))[:20],
            "state_file": file_status,
        }
    def save_state(self):
        """Persist critical state to survive app restarts."""
        try:
            with self._lock:
                # Convert tuple keys to strings and serialize OrderIntent objects
                state = {
                    "gtt_registry": {gtt_id: list(key) for gtt_id, key in self.gtt_registry.items()},
                    "buy_registry": {oid: list(key) for oid, key in self.buy_registry.items()},
                    "buy_credits": {"|".join(map(str, k)): v for k, v in self.buy_credits.items()},
                    "credited_order_ids": list(self._credited_order_ids),
                    "credited_qty_by_key": {"|".join(map(str, k)): v for k, v in self._credited_qty_by_key.items()},
                    "credited_count_by_key": {"|".join(map(str, k)): v for k, v in self._credited_count_by_key.items()},
                    "buy_queue": [
                        {
                            "intent": entry["intent"].model_dump(),
                            "trigger": entry["trigger"],
                            "tolerance": entry["tolerance"],
                            "queued_at": entry.get("queued_at"),
                        }
                        for entry in self.buy_queue
                    ],
                    "sell_queues": {
                        "|".join(map(str, key)): [intent.model_dump() for intent in queue]
                        for key, queue in self.sell_queues.items()
                    },
                }
            
            self.STATE_FILE.write_text(json.dumps(state, indent=2))
            msg = f"✅ State saved to {self.STATE_FILE}"
            print(f"[LINKER] {msg}")
            print(f"[LINKER]   gtt_registry={len(self.gtt_registry)}, buy_registry={len(self.buy_registry)}, queues={len(self.sell_queues)}")
            return msg
        except Exception as e:
            import traceback
            msg = f"❌ Failed to save state: {e}"
            print(f"[LINKER] {msg}")
            traceback.print_exc()
            return msg

    def load_state(self):
        """Restore state from previous session."""
        import os
        
        # Migration: check multiple possible old locations
        old_locations = [
            Path("linker_state.json"),  # Relative to current working directory
            Path.cwd() / "linker_state.json",  # Explicit cwd
            Path(__file__).parent / "linker_state.json",  # In services/ws/
            Path(__file__).parent.parent / "linker_state.json",  # In services/
        ]
        
        state_file_to_load = None
        #Dummy comment
        # Try new absolute path first
        if self.STATE_FILE.exists():
            state_file_to_load = self.STATE_FILE
            print(f"[LINKER] Found state at new path: {state_file_to_load}")
        else:
            # Look for old state file at various locations
            for old_path in old_locations:
                if old_path.exists():
                    print(f"[LINKER] Found old state file at: {old_path.absolute()}")
                    state_file_to_load = old_path
                    break
        
        if state_file_to_load is None:
            print(f"[LINKER] ℹ️  No saved state found")
            print(f"[LINKER]   Checked: {self.STATE_FILE}")
            print(f"[LINKER]   Checked cwd: {Path.cwd() / 'linker_state.json'}")
            return
        
        try:
            from models import OrderIntent
            state = json.loads(state_file_to_load.read_text())
            
            with self._lock:
                # Restore gtt_registry and buy_registry
                self.gtt_registry = {gtt_id: tuple(key) for gtt_id, key in state.get("gtt_registry", {}).items()}
                self.buy_registry = {oid: tuple(key) for oid, key in state.get("buy_registry", {}).items()}
                self.buy_credits = defaultdict(int, {
                    tuple(k.split("|")): v for k, v in state.get("buy_credits", {}).items()
                })
                self._credited_order_ids = set(state.get("credited_order_ids", []))
                self._credited_qty_by_key = defaultdict(int, {
                    tuple(k.split("|")): v for k, v in state.get("credited_qty_by_key", {}).items()
                })
                self._credited_count_by_key = defaultdict(int, {
                    tuple(k.split("|")): v for k, v in state.get("credited_count_by_key", {}).items()
                })
                
                # Restore buy_queue with OrderIntent objects
                for entry_data in state.get("buy_queue", []):
                    entry = {
                        "intent": OrderIntent(**entry_data["intent"]),
                        "trigger": entry_data["trigger"],
                        "tolerance": entry_data["tolerance"],
                        "queued_at": entry_data.get("queued_at"),
                    }
                    self.buy_queue.append(entry)
                
                # Restore sell_queues with OrderIntent objects
                for key_str, intents_data in state.get("sell_queues", {}).items():
                    key_parts = key_str.split("|")
                    key = (key_parts[0], key_parts[1], key_parts[2])
                    self.sell_queues[key] = deque([OrderIntent(**intent_dict) for intent_dict in intents_data])

                # Compact duplicates that may have accumulated in prior runs
                self._dedupe_queues_locked()
            
            print(f"[LINKER] ✅ State restored from {state_file_to_load.absolute()}")
            print(f"[LINKER]   gtt_registry={len(self.gtt_registry)}, buy_registry={len(self.buy_registry)}, buy_queue={len(self.buy_queue)}, sell_queues={len(self.sell_queues)}")
            print(f"[LINKER]   state_file_used: {state_file_to_load.absolute()}")
        except Exception as e:
            print(f"[LINKER] ⚠️ Failed to load state: {e}")
            import traceback
            traceback.print_exc()
            # Continue with empty state if load fails

    # -----------------------------
    # Maintenance helpers (Debug Panel)
    # -----------------------------
    def reset_state(self):
        """Clear all linker state (queues, registries, credits)."""
        with self._lock:
            self.buy_credits.clear()
            self.sell_queues.clear()
            self.buy_registry.clear()
            self.gtt_registry.clear()
            self._credited_order_ids.clear()
            self._credited_qty_by_key.clear()
            self._credited_count_by_key.clear()
        self.save_state()
        print("[LINKER] ⚠️ All linker state cleared")
        return "All linker state cleared"