# verify_kite.py
from kiteconnect import KiteConnect

API_KEY     = "9rn0bamx7itpbosd"
ACCESS_TOKEN= "yjAA2hE5mv6zU7lEFAFuipNrnBXRyEi0"

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

try:
    prof = kite.profile()
    print("OK -> user_id:", prof.get("user_id"))
except Exception as e:
    print("FAIL ->", repr(e))

target_symbol = "SENSEX25D1184800CE"
print(kite.instruments())