# services/orders/exit.py
# Build SELL/BUY intents to flatten all open NRML positions
# Fully compatible with WS-Linker architecture (tag="exit" ensures no linking)
from typing import List, Optional
from models import OrderIntent


def build_exit_intents_from_positions(kite, symbols_filter: Optional[List[str]] = None) -> List[OrderIntent]:
    """
    Build immediate MARKET NRML exit intents based on current NRML positions.

    Exit rules:
    -----------
    - qty > 0  → SELL that qty
    - qty < 0  → BUY that qty
    - All intents are MARKET NRML with tag='exit' so WS-linker ignores them.
    - symbols_filter: exit only specified symbols.
    """

    try:
        pos = kite.positions()
    except Exception:
        # Position fetch failed → return empty list.
        return []

    net = pos.get("net", [])
    intents: List[OrderIntent] = []

    for p in net:
        if p.get("product") != "NRML":
            continue

        qty = int(p.get("quantity") or 0)
        if qty == 0:
            continue

        symbol = p.get("tradingsymbol")
        exchange = p.get("exchange")

        if symbols_filter and symbol not in symbols_filter:
            continue

        txn_type = "SELL" if qty > 0 else "BUY"
        exit_qty = abs(qty)

        i = OrderIntent(
            exchange=exchange,
            symbol=symbol,
            txn_type=txn_type,
            qty=exit_qty,
            order_type="MARKET",
            price=None,
            trigger_price=None,
            product="NRML",
            validity="DAY",
            variety="regular",
            disclosed_qty=0,
            tag="exit",  # ensures WS linker never processes exit orders

            # GTT fields explicitly disabled
            gtt="NO",
            gtt_type=None,
            gtt_trigger=None,
            gtt_limit=None,
            gtt_trigger_1=None,
            gtt_trigger_2=None,
            gtt_limit_1=None,
            gtt_limit_2=None,
        )

        intents.append(i)

    return intents
