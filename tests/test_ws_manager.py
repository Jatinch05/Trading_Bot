from services.ws import ws_manager as W

class FakeTicker:
    def __init__(self, api_key, access_token):
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_order_update = None
    def connect(self, threaded=True):
        pass
    def close(self):
        pass

def test_ws_delta_and_callback(monkeypatch):
    events = []
    def cb(order_id, delta):
        events.append((order_id, delta))

    monkeypatch.setattr(W, "KiteTicker", FakeTicker)
    W.start("ak", "at", cb)

    # simulate an order update
    W._STATE["kt"].on_order_update(None, {
        "order_id": "O1", "filled_quantity": 3, "status": "COMPLETE", "tradingsymbol": "NIFTY"
    })
    assert events == [("O1", 3)]
    W.stop()
