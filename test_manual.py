"""
Manual Testing Script - Simulates Complete Flow Without Live Market
Run this to verify BUY-first, SELL-queue, event-driven release logic
"""

import sys
import io
from collections import defaultdict
from models import OrderIntent
from services.ws.linker import OrderLinker
from services.orders.placement import place_orders, place_released_sells

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


# ============================================================================
# FAKE KITE CLIENT (Market Closed Simulator)
# ============================================================================

class FakeKite:
    # GTT Constants matching KiteConnect SDK
    GTT_TYPE_SINGLE = "single"
    GTT_TYPE_OCO = "two-leg"
    
    def __init__(self):
        self.orders = {}
        self.gtts = {}
        self.order_counter = 100000
        self.gtt_counter = 200000
        self._positions = {"net": []}
        
    def place_order(self, **kwargs):
        self.order_counter += 1
        order_id = f"ORD{self.order_counter}"
        self.orders[order_id] = {
            "order_id": order_id,
            "status": "PENDING",
            "filled_quantity": 0,
            **kwargs
        }
        print(f"  📤 PLACED: {kwargs['transaction_type']} {kwargs['quantity']} {kwargs['tradingsymbol']} @ {kwargs['order_type']} (ID: {order_id})")
        return order_id
    
    def place_gtt(self, **kwargs):
        self.gtt_counter += 1
        gtt_id = f"GTT{self.gtt_counter}"
        self.gtts[gtt_id] = {
            "id": gtt_id,
            "status": "active",
            "trigger_type": kwargs.get("trigger_type"),
            "tradingsymbol": kwargs.get("tradingsymbol"),
            "orders": kwargs.get("orders", []),
            "child_order_id": None,
        }
        print(f"  📤 GTT PLACED: {kwargs['trigger_type'].upper()} on {kwargs['tradingsymbol']} (ID: {gtt_id})")
        return {"id": gtt_id}
    
    def simulate_order_fill(self, order_id, filled_qty=None):
        """Simulate order completion"""
        if order_id not in self.orders:
            print(f"  ❌ Order {order_id} not found")
            return None
        
        order = self.orders[order_id]
        if filled_qty is None:
            filled_qty = order["quantity"]
        
        order["status"] = "COMPLETE"
        order["filled_quantity"] = filled_qty
        print(f"  ✅ FILLED: {order_id} - {order['transaction_type']} {filled_qty} {order['tradingsymbol']}")
        
        return {
            "order_id": order_id,
            "status": "COMPLETE",
            "transaction_type": order["transaction_type"],
            "filled_quantity": filled_qty,
            "tradingsymbol": order["tradingsymbol"],
        }
    
    def simulate_gtt_trigger(self, gtt_id):
        """Simulate GTT trigger creating child order"""
        if gtt_id not in self.gtts:
            print(f"  ❌ GTT {gtt_id} not found")
            return None
        
        gtt = self.gtts[gtt_id]
        self.order_counter += 1
        child_order_id = f"ORD{self.order_counter}"
        
        # Create child order from GTT definition
        child_order_def = gtt["orders"][0]  # First leg
        self.orders[child_order_id] = {
            "order_id": child_order_id,
            "status": "PENDING",
            "filled_quantity": 0,
            "transaction_type": child_order_def["transaction_type"],
            "quantity": child_order_def["quantity"],
            "tradingsymbol": gtt["tradingsymbol"],
        }
        
        gtt["status"] = "triggered"
        gtt["child_order_id"] = child_order_id
        
        print(f"  🔔 GTT TRIGGERED: {gtt_id} → created child order {child_order_id}")
        
        return {
            "id": gtt_id,
            "status": "triggered",
            "orders": [{
                "result": {
                    "order_result": {
                        "order_id": child_order_id,
                        "filled_quantity": 0,
                    }
                }
            }]
        }
    
    def get_gtts(self):
        """Return all GTTs (for watcher polling)"""
        return list(self.gtts.values())
    
    def positions(self):
        return self._positions


# ============================================================================
# TEST SCENARIOS
# ============================================================================

def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)


def test_scenario_1_normal_buy_sell():
    """BUY MARKET → SELL LIMIT (normal orders, linked)"""
    print_section("SCENARIO 1: Normal BUY → Queued SELL → WS Fill → Release")
    
    fake_kite = FakeKite()
    linker = OrderLinker()
    
    # Track released sells
    released_sells = []
    def on_release(sells):
        released_sells.extend(sells)
        print(f"\n  🚀 LINKER RELEASED {len(sells)} SELL(S):")
        for s in sells:
            print(f"     - {s.symbol} SELL {s.qty} @ {s.order_type}")
    
    linker.set_release_callback(on_release)
    
    # Create intents
    intents = [
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="BUY", qty=50,
            order_type="MARKET", price=None, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="SELL", qty=25,
            order_type="LIMIT", price=23500.0, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="SELL", qty=25,
            order_type="LIMIT", price=23600.0, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
    ]
    
    print("\n1️⃣  Placing orders (BUYs immediate, SELLs queued)...")
    results = place_orders(fake_kite, intents, linker=linker, live=True)
    
    print(f"\n📊 Results: {len(results)} operations")
    for r in results:
        print(f"   {r}")
    
    print(f"\n📋 Linker state:")
    snap = linker.snapshot()
    print(f"   Credits: {snap['credits']}")
    print(f"   Queued: {snap['queues']}")
    
    # Simulate WS order fill
    print("\n2️⃣  Simulating WebSocket: BUY order completes...")
    buy_order_id = results[0]["order_id"]
    ws_event = fake_kite.simulate_order_fill(buy_order_id, filled_qty=50)
    
    # Manually trigger linker credit (simulating WSManager.on_order_update)
    linker.on_buy_fill(ws_event["order_id"], ws_event["filled_quantity"])
    
    print(f"\n📋 Linker state after fill:")
    snap = linker.snapshot()
    print(f"   Credits: {snap['credits']}")
    print(f"   Queued: {snap['queues']}")
    
    # Place released sells
    if released_sells:
        print("\n3️⃣  Placing released SELLs...")
        sell_results = place_released_sells(fake_kite, released_sells, live=True)
        for r in sell_results:
            print(f"   {r}")
    
    print("\n✅ SCENARIO 1 COMPLETE")
    return fake_kite, linker


def test_scenario_2_gtt_buy_sell():
    """BUY GTT SINGLE → SELL GTT SINGLE (both GTT, linked)"""
    print_section("SCENARIO 2: GTT BUY → Queued GTT SELL → GTT Trigger → WS Child Fill → Release")
    
    fake_kite = FakeKite()
    linker = OrderLinker()
    
    released_sells = []
    def on_release(sells):
        released_sells.extend(sells)
        print(f"\n  🚀 LINKER RELEASED {len(sells)} SELL(S)")
    
    linker.set_release_callback(on_release)
    
    intents = [
        OrderIntent(
            exchange="NFO", symbol="BANKNIFTY25JANFUT", txn_type="BUY", qty=30,
            order_type="LIMIT", price=None, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group2", gtt="YES", gtt_type="SINGLE",
            gtt_trigger=50000.0, gtt_limit=49950.0
        ),
        OrderIntent(
            exchange="NFO", symbol="BANKNIFTY25JANFUT", txn_type="SELL", qty=30,
            order_type="LIMIT", price=None, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group2", gtt="YES", gtt_type="SINGLE",
            gtt_trigger=51000.0, gtt_limit=50950.0
        ),
    ]
    
    print("\n1️⃣  Placing orders...")
    results = place_orders(fake_kite, intents, linker=linker, live=True)
    for r in results:
        print(f"   {r}")
    
    print(f"\n📋 Linker state:")
    snap = linker.snapshot()
    print(f"   GTT registry: {snap['gtt_registry']}")
    print(f"   Queued: {snap['queues']}")
    
    # Simulate GTT trigger
    print("\n2️⃣  Simulating GTT trigger (market reaches 50000)...")
    gtt_buy_id = results[0]["order_id"]
    gtt_data = fake_kite.simulate_gtt_trigger(gtt_buy_id)
    
    # Simulate GTT watcher binding child order
    print("\n3️⃣  GTT Watcher binds child order to linker...")
    child_order_id = gtt_data["orders"][0]["result"]["order_result"]["order_id"]
    linker.bind_gtt_child(gtt_buy_id, child_order_id)
    
    print(f"\n📋 Linker state after binding:")
    snap = linker.snapshot()
    print(f"   Buy registry: {snap['buy_registry']}")
    
    # Simulate child order fill via WS
    print("\n4️⃣  WebSocket: Child order completes...")
    ws_event = fake_kite.simulate_order_fill(child_order_id, filled_qty=30)
    linker.on_buy_fill(ws_event["order_id"], ws_event["filled_quantity"])
    
    print(f"\n📋 Linker state after fill:")
    snap = linker.snapshot()
    print(f"   Credits: {snap['credits']}")
    print(f"   Queued: {snap['queues']}")
    
    # Place released GTT sell
    if released_sells:
        print("\n5️⃣  Placing released GTT SELL...")
        sell_results = place_released_sells(fake_kite, released_sells, live=True)
        for r in sell_results:
            print(f"   {r}")
    
    print("\n✅ SCENARIO 2 COMPLETE")
    return fake_kite, linker


def test_scenario_3_exit_orders():
    """Exit orders bypass linking"""
    print_section("SCENARIO 3: Exit Orders (No Linking)")
    
    fake_kite = FakeKite()
    linker = OrderLinker()
    
    # Simulate existing positions
    fake_kite._positions = {
        "net": [
            {"exchange": "NFO", "tradingsymbol": "NIFTY25JANFUT", "product": "NRML", "quantity": 50, "pnl": 1200.0},
        ]
    }
    
    from services.orders.exit import build_exit_intents_from_positions
    
    print("\n1️⃣  Building exit intents from positions...")
    exit_intents = build_exit_intents_from_positions(fake_kite)
    
    for intent in exit_intents:
        print(f"   {intent.symbol} {intent.txn_type} {intent.qty} (tag={intent.tag})")
    
    print("\n2️⃣  Placing exit orders (should NOT queue)...")
    results = place_orders(fake_kite, exit_intents, linker=linker, live=True)
    
    for r in results:
        print(f"   {r}")
    
    print(f"\n📋 Linker state (should be empty):")
    snap = linker.snapshot()
    print(f"   Credits: {snap['credits']}")
    print(f"   Queued: {snap['queues']}")
    print(f"   Buy registry: {snap['buy_registry']}")
    
    print("\n✅ SCENARIO 3 COMPLETE")
    return fake_kite, linker


def test_scenario_4_partial_fills():
    """Partial BUY fill releases only matching SELL qty"""
    print_section("SCENARIO 4: Partial Fills & Quantity Awareness")
    
    fake_kite = FakeKite()
    linker = OrderLinker()
    
    released_sells = []
    def on_release(sells):
        released_sells.extend(sells)
        print(f"\n  🚀 RELEASED: {sum(s.qty for s in sells)} total qty")
    
    linker.set_release_callback(on_release)
    
    intents = [
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="BUY", qty=100,
            order_type="MARKET", price=None, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="SELL", qty=40,
            order_type="LIMIT", price=23500.0, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="SELL", qty=30,
            order_type="LIMIT", price=23600.0, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
        OrderIntent(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="SELL", qty=30,
            order_type="LIMIT", price=23700.0, trigger_price=None,
            product="NRML", validity="DAY", variety="regular", disclosed_qty=0,
            tag="link:group1", gtt="NO", gtt_type=None
        ),
    ]
    
    print("\n1️⃣  Placing 1 BUY(100) + 3 SELLs(40+30+30)...")
    results = place_orders(fake_kite, intents, linker=linker, live=True)
    
    print(f"\n📋 Queued: {linker.snapshot()['queues']}")
    
    # Partial fill 1
    print("\n2️⃣  BUY fills 40 qty (partial)...")
    buy_order_id = results[0]["order_id"]
    linker.on_buy_fill(buy_order_id, 40)
    
    print(f"   Credits: {linker.snapshot()['credits']}")
    print(f"   Queued: {linker.snapshot()['queues']}")
    print(f"   Released count: {len(released_sells)}")
    
    # Partial fill 2
    print("\n3️⃣  BUY fills additional 30 qty...")
    linker.on_buy_fill(buy_order_id, 30)
    
    print(f"   Credits: {linker.snapshot()['credits']}")
    print(f"   Queued: {linker.snapshot()['queues']}")
    print(f"   Released count: {len(released_sells)}")
    
    # Final fill
    print("\n4️⃣  BUY fills final 30 qty (complete)...")
    linker.on_buy_fill(buy_order_id, 30)
    
    print(f"   Credits: {linker.snapshot()['credits']}")
    print(f"   Queued: {linker.snapshot()['queues']}")
    print(f"   Released count: {len(released_sells)}")
    
    print("\n✅ SCENARIO 4 COMPLETE")
    return fake_kite, linker


# ============================================================================
# MAIN RUNNER
# ============================================================================

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════╗
║         NRML Order Engine - Manual Testing Suite                ║
║         (Market-closed simulation)                               ║
╚══════════════════════════════════════════════════════════════════╝
    """)
    
    try:
        test_scenario_1_normal_buy_sell()
        test_scenario_2_gtt_buy_sell()
        test_scenario_3_exit_orders()
        test_scenario_4_partial_fills()
        
        print("\n" + "="*70)
        print("  🎉 ALL SCENARIOS PASSED")
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
