"""
Inspect KiteConnect's _get_gtt_payload to see exact GTT order field requirements
"""
import inspect
from kiteconnect import KiteConnect

print("=" * 80)
print("KITECONNECT GTT PAYLOAD VALIDATION")
print("=" * 80)

# Get the source code of _get_gtt_payload
try:
    source = inspect.getsource(KiteConnect._get_gtt_payload)
    print("\n_get_gtt_payload() source code:\n")
    print(source)
except Exception as e:
    print(f"Error getting source: {e}")

print("\n" + "=" * 80)
print("VALIDATION ANALYSIS")
print("=" * 80)

# Also check place_gtt
print("\nplace_gtt() source:\n")
try:
    source = inspect.getsource(KiteConnect.place_gtt)
    print(source)
except Exception as e:
    print(f"Error: {e}")
