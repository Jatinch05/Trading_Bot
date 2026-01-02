# services/ws/buy_monitor.py
import threading
import time
from typing import Optional

class BuyMonitor:
    """Monitor prices for queued BUYs and place them when triggers are hit."""
    
    def __init__(self, kite, linker, interval_sec: float = 2.5):
        self.kite = kite
        self.linker = linker
        self.interval_sec = interval_sec
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._placement_callback = None  # Callback to place BUYs when triggered
        self.token_exchanged_at: float = None  # Set by runtime to track token age
        
    def set_placement_callback(self, cb):
        """Set a callback to place BUYs when they're ready: cb(list[OrderIntent])"""
        self._placement_callback = cb
        
    def start(self):
        """Start monitoring prices for queued BUYs."""
        if self._running:
            print("[BUY_MONITOR] Already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"[BUY_MONITOR] Started (interval={self.interval_sec}s)")
    
    def stop(self):
        """Stop monitoring."""
        if not self._running:
            return
        
        print("[BUY_MONITOR] Stopping...")
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[BUY_MONITOR] Stopped")
    
    def _monitor_loop(self):
        """Background loop that checks prices and triggers placement."""
        while self._running:
            try:
                self._check_and_place()
            except Exception as e:
                print(f"[BUY_MONITOR] Error in monitor loop: {e}")
                import traceback
                traceback.print_exc()
            
            time.sleep(self.interval_sec)
    
    def _check_and_place(self):
        """Check queued BUYs against current prices and place if triggered."""
        # Log token age periodically if available
        if self.token_exchanged_at is not None:
            token_age_hours = (time.time() - self.token_exchanged_at) / 3600
            if token_age_hours > 23.5:
                print(f"[BUY_MONITOR] ⏰ Token age: {token_age_hours:.1f}h (>24h expiry, consider new token)")
        
        # Get list of symbols in buy queue
        queued_symbols = set()
        with self.linker._lock:
            for entry in self.linker.buy_queue:
                queued_symbols.add(entry["intent"].symbol)
        
        if not queued_symbols:
            return  # Nothing queued
        
        # Fetch LTP for all queued symbols
        prices_dict = {}
        try:
            # Build keys for kite.ltp: "EXCHANGE:SYMBOL"
            keys = []
            symbol_to_key = {}
            for entry in self.linker.buy_queue:
                intent = entry["intent"]
                key = f"{intent.exchange}:{intent.symbol}"
                keys.append(key)
                symbol_to_key[intent.symbol] = key
            
            if not keys:
                return
            
            # Fetch LTP batch
            ltp_data = self.kite.ltp(keys)
            
            # Parse into {symbol: ltp}
            for symbol, key in symbol_to_key.items():
                if key in ltp_data:
                    ltp = ltp_data[key].get("last_price")
                    if ltp is not None:
                        prices_dict[symbol] = float(ltp)
        
        except Exception as e:
            print(f"[BUY_MONITOR] Failed to fetch LTP: {e}")
            return
        
        # Check triggers
        ready_intents = self.linker.check_buy_triggers(prices_dict)
        
        if ready_intents and self._placement_callback:
            print(f"[BUY_MONITOR] Placing {len(ready_intents)} triggered BUY(s)")
            self._placement_callback(ready_intents)
    
    def snapshot(self):
        """Return current state for debugging."""
        return {
            "running": self._running,
            "interval_sec": self.interval_sec,
            "has_callback": self._placement_callback is not None,
        }
