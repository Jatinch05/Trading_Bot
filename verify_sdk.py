"""
Verify KiteConnect SDK compliance
Inspects actual constants, methods, and response structures
"""

import sys

print("=" * 70)
print("KITECONNECT SDK VERIFICATION")
print("=" * 70)

# Check KiteConnect version
try:
    import kiteconnect
    print(f"\n✓ KiteConnect installed")
    print(f"  Version: {kiteconnect.__version__ if hasattr(kiteconnect, '__version__') else 'Unknown'}")
except ImportError as e:
    print(f"\n✗ KiteConnect not installed: {e}")
    sys.exit(1)

# Check KiteConnect class
from kiteconnect import KiteConnect

print(f"\n✓ KiteConnect class imported")

# List all GTT-related constants
print(f"\n📋 GTT Constants in KiteConnect:")
gtt_attrs = [attr for attr in dir(KiteConnect) if "GTT" in attr.upper()]
if gtt_attrs:
    for attr in gtt_attrs:
        val = getattr(KiteConnect, attr, None)
        print(f"  - {attr} = {val}")
else:
    print(f"  ⚠ No GTT constants found in KiteConnect class")
    print(f"  Available constants:")
    for attr in sorted(dir(KiteConnect)):
        if attr.isupper() and not attr.startswith("_"):
            val = getattr(KiteConnect, attr)
            if isinstance(val, (int, str)):
                print(f"    - {attr} = {val}")

# Check place_gtt method signature
print(f"\n📋 place_gtt method signature:")
import inspect
try:
    sig = inspect.signature(KiteConnect.place_gtt)
    print(f"  {sig}")
except Exception as e:
    print(f"  ⚠ Could not get signature: {e}")

# Check place_gtt docstring
print(f"\n📋 place_gtt docstring:")
if KiteConnect.place_gtt.__doc__:
    print(KiteConnect.place_gtt.__doc__)
else:
    print(f"  (No docstring)")

# List place_gtt method
print(f"\n📋 place_gtt method source:")
try:
    source = inspect.getsource(KiteConnect.place_gtt)
    # Print first 50 lines
    lines = source.split('\n')[:50]
    for line in lines:
        print(line)
except Exception as e:
    print(f"  ⚠ Could not get source: {e}")

# Check response types
print(f"\n📋 Testing response structure (without live credentials):")
print(f"  Creating dummy KiteConnect instance...")
try:
    dummy_kite = KiteConnect(api_key="dummy")
    print(f"  Instance type: {type(dummy_kite)}")
    print(f"  place_gtt method: {type(dummy_kite.place_gtt)}")
except Exception as e:
    print(f"  Note: {e}")

# Summary
print(f"\n" + "=" * 70)
print("COMPLIANCE CHECKLIST:")
print("=" * 70)
print(f"1. GTT_TYPE_SINGLE constant exists: {'GTT_TYPE_SINGLE' in dir(KiteConnect)}")
print(f"2. GTT_TYPE_OCO constant exists: {'GTT_TYPE_OCO' in dir(KiteConnect)}")
print(f"3. place_gtt method exists: {'place_gtt' in dir(KiteConnect)}")
print(f"4. Response structure: Check above for place_gtt docstring")
print(f"\n⚠ IMPORTANT: Run with live KiteConnect to verify actual response structure!")
print("=" * 70)
