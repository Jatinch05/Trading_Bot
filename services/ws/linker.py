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
        # Debug: show path on init
        print(f"[LINKER] STATE_FILE configured: {self.STATE_FILE}")

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
        with self._lock:
            if oid in self._credited_order_ids:
                print(f"[LINKER] Duplicate credit ignored: order_id={oid} source={source}")
                return []

            key = self.buy_registry.get(oid)
            if not key:
                # If we can't map this order_id yet, don't mark it credited
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
            self.save_state()

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
            sig = (
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
                intent.gtt_trigger,
                intent.gtt_limit,
                intent.gtt_trigger_1,
                intent.gtt_limit_1,
                intent.gtt_trigger_2,
                intent.gtt_limit_2,
            )
            existing_sigs = {
                (
                    s.symbol,
                    s.exchange,
                    s.qty,
                    s.order_type,
                    s.price,
                    s.trigger_price,
                    s.product,
                    s.validity,
                    s.variety,
                    s.disclosed_qty,
                    s.tag,
                    s.gtt,
                    s.gtt_type,
                    getattr(s, "gtt_trigger", None),
                    getattr(s, "gtt_limit", None),
                    getattr(s, "gtt_trigger_1", None),
                    getattr(s, "gtt_limit_1", None),
                    getattr(s, "gtt_trigger_2", None),
                    getattr(s, "gtt_limit_2", None),
                )
                for s in q
            }

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
                
                # Restore sell_queues with OrderIntent objects
                for key_str, intents_data in state.get("sell_queues", {}).items():
                    key_parts = key_str.split("|")
                    key = (key_parts[0], key_parts[1], key_parts[2])
                    self.sell_queues[key] = deque([OrderIntent(**intent_dict) for intent_dict in intents_data])
            
            print(f"[LINKER] ✅ State restored from {state_file_to_load.absolute()}")
            print(f"[LINKER]   gtt_registry={len(self.gtt_registry)}, buy_registry={len(self.buy_registry)}, queues={len(self.sell_queues)}")
            print(f"[LINKER]   state_file_used: {state_file_to_load.absolute()}")
        except Exception as e:
            print(f"[LINKER] ⚠️ Failed to load state: {e}")
            import traceback
            traceback.print_exc()
            # Continue with empty state if load fails