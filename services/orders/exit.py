from models import OrderIntent

def build_exit_intents_from_positions(kite, symbols_filter=None):
    intents = []
    try:
        pos = kite.positions() or {}
        allpos = (pos.get("net") or []) + (pos.get("day") or [])
    except Exception:
        allpos = []
    seen = set()
    for p in allpos:
        exch = str(p.get("exchange") or "").upper()
        sym  = str(p.get("tradingsymbol") or "").upper()
        prod = str(p.get("product") or "").upper()
        netq = int(p.get("quantity") or p.get("net_quantity") or 0)
        key = (exch, sym, prod)
        if key in seen: continue
        seen.add(key)
        if prod != "NRML": continue
        if symbols_filter and sym not in symbols_filter: continue
        if netq == 0: continue
        txn = "SELL" if netq > 0 else "BUY"
        qty = abs(int(netq))
        intents.append(OrderIntent(
            symbol=sym, exchange=exch, txn_type=txn, qty=qty, order_type="MARKET",
            price=None, trigger_price=None, product="NRML", validity="DAY", variety="regular",
            disclosed_qty=None, tag="EXIT_ALL" if not symbols_filter else "EXIT_SEL",
            gtt="", gtt_type=""
        ))
    return intents
