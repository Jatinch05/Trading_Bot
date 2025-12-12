import pytest
from types import SimpleNamespace
from uuid import uuid4
# tests/conftest.py (top)
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Minimal OrderIntent factory to avoid importing heavy deps in tests
class OI(SimpleNamespace):
    pass

@pytest.fixture
def oi():
    def _mk(**kw):
        defaults = dict(
            exchange="NFO", symbol="NIFTY25JANFUT", txn_type="BUY", qty=1,
            order_type="MARKET", price=None, trigger_price=None,
            product="NRML", validity="DAY", variety="regular",
            disclosed_qty=0, tag=None, gtt="NO", gtt_type=None,
            gtt_trigger=None, gtt_limit=None, gtt_trigger_1=None, gtt_trigger_2=None,
            gtt_limit_1=None, gtt_limit_2=None,
        )
        defaults.update(kw)
        return OI(**defaults)
    return _mk

@pytest.fixture
def fake_kite():
    class FK:
        def __init__(self):
            self.orders = []
            self.gtts = {}
            self._ltp = {}
            self._positions = {"net": []}
            self._holdings = []

        def set_ltp(self, m):
            self._ltp = m

        def ltp(self, keys):
            return {k: {"last_price": self._ltp.get(k, 100.0)} for k in keys}

        def place_order(self, **payload):
            self.orders.append(payload)
            return {"order_id": f"ORD-{uuid4().hex[:10]}"}

        def place_gtt(self, **payload):
            tid = f"T-{uuid4().hex[:8]}"
            self.gtts[tid] = {"payload": payload, "status": "active", "orders": []}
            return {"trigger_id": tid}

        # watcher fetch api: get_gtt / gtt
        def get_gtt(self, trig_id):
            return self.gtts.get(str(trig_id))

        def positions(self):
            return self._positions

        def holdings(self):
            return self._holdings

        def profile(self):
            return {"user_id": "FAKE"}

    return FK()
