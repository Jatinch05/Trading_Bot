"""
Standalone WebSocket test to verify order update delivery.
Runs without Streamlit; helps isolate WS issues.

Usage:
1. Run: python test_websocket.py
2. Follow prompts to get login URL and request_token
3. Script will exchange for access_token and test WebSocket
4. Place/execute a BUY order in live market during the 60-second listen window
5. Observe if WS receives COMPLETE event
"""

import sys
import time
from kiteconnect import KiteTicker
from services.auth import KiteAuth


# Event tracking
events = []

def log(msg):
    """Log to console and buffer."""
    print(msg)
    events.append(msg)
    if len(events) > 100:
        events.pop(0)

def on_ticks(ws, ticks):
    """Tick data (not needed for order updates)."""
    pass

def on_connect(ws, resp):
    """Called when WebSocket connects."""
    log(f"✅ [WS_CONNECT] resp={resp}")
    try:
        ws.subscribe([])
    except Exception as e:
        log(f"❌ [WS_SUBSCRIBE_ERROR] {e}")

def on_close(ws, code, reason):
    """Called when WebSocket closes."""
    log(f"⚠️  [WS_CLOSE] code={code} reason={reason}")

def on_error(ws, code, reason):
    """Called on WebSocket error."""
    log(f"❌ [WS_ERROR] code={code} reason={reason}")

def on_reconnect(ws, attempt_count):
    """Called when attempting to reconnect."""
    log(f"🔄 [WS_RECONNECT] attempt={attempt_count}")

def on_noreconnect(ws):
    """Called when reconnect exhausted."""
    log(f"❌ [WS_NORECONNECT] Giving up on reconnection")

def on_order_update(ws, data):
    """Called for every order update."""
    log(f"📬 [WS_ORDER_UPDATE] {data}")
    
    # Parse key fields
    order_id = data.get("order_id")
    status = data.get("status")
    txn_type = data.get("transaction_type")
    symbol = data.get("tradingsymbol")
    filled_qty = data.get("filled_quantity", 0)
    
    log(f"   → order_id={order_id}, status={status}, txn_type={txn_type}, symbol={symbol}, filled_qty={filled_qty}")
    
    # If BUY fills, this is what we care about
    if status == "COMPLETE" and txn_type == "BUY":
        log(f"✅ [BUY_COMPLETE] {order_id} - {filled_qty} shares of {symbol}")
    elif status == "COMPLETE":
        log(f"ℹ️  [OTHER_COMPLETE] {txn_type} {order_id}")

def main():
    log("=" * 80)
    log("WEBSOCKET ORDER UPDATE TEST")
    log("=" * 80)
    
    # Step 1: Get API credentials from config
    log(f"\n📋 Step 1: Load API credentials from config")
    api_key = "9rn0bamx7itpbosd"
    api_secret = "yadu2gqavyd88lv6jsjwjqgao742lc2c"
    
    if not api_key or not api_secret:
        log(f"❌ ERROR: API_KEY or API_SECRET not in config!")
        return
    
    log(f"   ✅ API_KEY: {api_key[:10]}...")
    
    # Step 2: Get request_token and exchange for access_token
    log(f"\n📋 Step 2: Exchange request_token for access_token")
    request_token = input("Paste your request_token: ").strip()
    
    if not request_token:
        log(f"❌ No request_token provided")
        return
    
    log(f"   request_token: {request_token[:20]}...")
    
    try:
        auth = KiteAuth(api_key, api_secret)
        access_token = auth.exchange_request_token(request_token)
        log(f"   ✅ access_token obtained: {access_token[:20]}...")
    except Exception as e:
        log(f"   ❌ Token exchange failed: {e}")
        return
    
    # Step 5: Initialize KiteTicker with access_token
    log(f"\n🔧 Step 5: Initialize KiteTicker with access_token")
    try:
        kws = KiteTicker(api_key, access_token)
        log(f"   ✅ KiteTicker created")
    except Exception as e:
        log(f"   ❌ KiteTicker init failed: {e}")
        return
    
    # Attach callbacks
    log(f"\n🎯 Step 6: Attach WebSocket callbacks")
    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close
    kws.on_error = on_error
    kws.on_reconnect = on_reconnect
    kws.on_noreconnect = on_noreconnect
    kws.on_order_update = on_order_update
    log(f"   ✅ All callbacks attached")
    
    # Connect
    log(f"\n🚀 Step 7: Connect to WebSocket (threaded)")
    try:
        kws.connect(threaded=True)
        log(f"   ✅ Connection initiated")
    except Exception as e:
        log(f"   ❌ Connection failed: {e}")
        return
    
    # Wait for events
    log(f"\n⏳ Step 8: Listening for order updates (60 seconds)")
    log(f"   Place/execute a BUY order in live market now!")
    log(f"   This test will print any order updates received.\n")
    
    start_time = time.time()
    update_count = 0
    
    while time.time() - start_time < 60:
        current_updates = len([e for e in events if "[WS_ORDER_UPDATE]" in e])
        if current_updates > update_count:
            update_count = current_updates
        
        time.sleep(1)
    
    log(f"\n⏹️  Test complete.")
    log(f"\n📊 Summary:")
    log(f"   Events received: {len(events)}")
    log(f"   Order updates: {len([e for e in events if '[WS_ORDER_UPDATE]' in e])}")
    log(f"   Errors: {len([e for e in events if '❌' in e])}")
    
    # Disconnect
    log(f"\n🔌 Closing WebSocket...")
    try:
        kws.close()
        log(f"   ✅ Closed")
    except Exception as e:
        log(f"   ⚠️  Close error: {e}")
    
    # Print event log
    log(f"\n" + "=" * 80)
    log("EVENT LOG:")
    log("=" * 80)
    for event in events:
        print(event)

if __name__ == "__main__":
    main()
