from services.orders.exit import build_exit_intents_from_positions

def test_exit_builds_nrml_market_sides(fake_kite):
    fake_kite._positions = {"net":[
        {"exchange":"NFO","tradingsymbol":"NIFTY","product":"NRML","quantity": 3},
        {"exchange":"NFO","tradingsymbol":"BANKNIFTY","product":"NRML","quantity":-5},
    ]}
    intents = build_exit_intents_from_positions(fake_kite)
    sides = sorted([(i.symbol, i.txn_type, i.qty) for i in intents])
    assert sides == [("BANKNIFTY","BUY",5),("NIFTY","SELL",3)]
