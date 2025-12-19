"""Debug script to understand linker state recovery and GTT watcher sync."""

import json
from pathlib import Path
from services.ws.linker import OrderLinker
from services.ws.gtt_watcher import GTTWatcher

def debug_linker_state():
    """Check what's in the saved linker state file."""
    state_file = Path("linker_state.json")
    if state_file.exists():
        print("=== SAVED LINKER STATE ===")
        print(state_file.read_text())
        print()
    else:
        print("❌ No linker_state.json found\n")

def debug_linker_recovery():
    """Test linker state recovery."""
    linker = OrderLinker()
    print("=== LINKER RECOVERY TEST ===")
    print(f"Before load_state():")
    print(f"  gtt_registry: {dict(linker.gtt_registry)}")
    print(f"  buy_registry: {dict(linker.buy_registry)}")
    print()
    
    linker.load_state()
    print(f"After load_state():")
    print(f"  gtt_registry: {dict(linker.gtt_registry)}")
    print(f"  buy_registry: {dict(linker.buy_registry)}")
    print(f"  buy_credits keys: {list(linker.buy_credits.keys())}")
    print()
    
    return linker

def debug_gtt_watcher_sync(linker):
    """Test GTT watcher sync with linker."""
    print("=== GTT WATCHER SYNC TEST ===")
    print(f"Linker gtt_registry has {len(linker.gtt_registry)} GTTs")
    print(f"GTT IDs: {list(linker.gtt_registry.keys())}")
    print()
    
    # Simulate GTT watcher binding
    print("Simulating GTT watcher bind_linker():")
    gtt_watcher = GTTWatcher(None)
    gtt_watcher.bind_linker(linker)
    
    print(f"After bind_linker():")
    print(f"  gtt_watcher.pending: {gtt_watcher.pending}")
    print(f"  gtt_watcher.resolved: {gtt_watcher.resolved}")
    print()

if __name__ == "__main__":
    debug_linker_state()
    linker = debug_linker_recovery()
    debug_gtt_watcher_sync(linker)
