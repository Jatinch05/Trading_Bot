from services.ws import linker as L

def test_defer_and_release_regular_sell(monkeypatch):
    placed = []
    def place_regular(payload):
        placed.append(("REG", payload))
        return "S-1"
    def place_gtt(payload):
        placed.append(("GTT", payload))
        return "G-1"

    L.set_placers(place_regular, place_gtt)
    L.start()

    # Defer SELL for group g1
    intents = [{
        "exchange": "NFO", "symbol": "NIFTY25JANFUT", "quantity": 2,
        "tag": "link:g1", "kind": "regular", "meta": {"order_type": "MARKET"}
    }]
    L.defer_sells(intents)

    # Register BUY and credit
    L.register_buy_order("B1", "NFO", "NIFTY25JANFUT", "link:g1")
    L.credit_by_order_id("B1", 2)

    snap = L.snapshot()
    assert len(placed) == 1
    assert "NFO" in placed[0][1]["exchange"]
    assert snap["credits"]  # key exists
