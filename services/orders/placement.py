# services/orders/placement.py

from typing import List
from models import OrderIntent


def _resolve_last_price(kite, intent: OrderIntent, fallback: float) -> float:
    """Derive last_price for place_gtt.

    Kite validates `last_price` relative to trigger(s). Using a value on the wrong
    side can cause errors like:
      "Trigger cannot be created with the first trigger price more than the last price."

    Strategy:
    1) Prefer live LTP from `kite.quote`.
    2) If missing/too close/wrong-side, nudge to the *correct* side:
       - BUY triggers typically must be ABOVE last_price  -> keep last_price < trigger
       - SELL triggers typically must be BELOW last_price -> keep last_price > trigger
    """

    last_price = fallback
    quote_key = f"{intent.exchange}:{intent.symbol}"
    try:
        quote = kite.quote(quote_key)
        instrument = quote.get(quote_key, {}) if isinstance(quote, dict) else {}
        candidate = instrument.get("last_price") or instrument.get("last_traded_price")
        if candidate is not None and str(candidate).strip() != "":
            last_price = float(candidate)
    except Exception:
        last_price = fallback

    if last_price is None:
        last_price = fallback

    trigger = float(fallback)
    current = float(last_price)

    # Kite requires >0.25% difference; use 0.3% to be safe, minimum 0.5
    threshold_pct = 0.003  # 0.3%
    min_epsilon = 0.5
    epsilon = max(abs(trigger) * threshold_pct, min_epsilon)

    # If too close to trigger, push away on the correct side
    if abs(current - trigger) < abs(trigger) * threshold_pct:
        if intent.txn_type == "BUY":
            current = trigger - epsilon
        else:
            current = trigger + epsilon

    # Ensure correct-side relationship even if quote returned wrong-side
    if intent.txn_type == "BUY" and current >= trigger:
        current = trigger - epsilon
    if intent.txn_type == "SELL" and current <= trigger:
        current = trigger + epsilon

    return float(current)


def _resolve_last_price_for_oco(kite, intent: OrderIntent, trig_a: float, trig_b: float) -> float:
    """Compute a valid last_price for OCO.

    For OCO, Kite expects last_price to lie between the two trigger values.
    We use live LTP if available; otherwise we use the midpoint, and then clamp
    inside (low, high) with a small epsilon.
    """

    low = float(min(trig_a, trig_b))
    high = float(max(trig_a, trig_b))
    mid = (low + high) / 2.0

    last_price = mid
    quote_key = f"{intent.exchange}:{intent.symbol}"
    try:
        quote = kite.quote(quote_key)
        instrument = quote.get(quote_key, {}) if isinstance(quote, dict) else {}
        candidate = instrument.get("last_price") or instrument.get("last_traded_price")
        if candidate is not None:
            last_price = float(candidate)
    except Exception:
        last_price = mid

    # Clamp strictly between triggers
    min_epsilon = 0.5
    if last_price <= low:
        last_price = low + min_epsilon
    if last_price >= high:
        last_price = high - min_epsilon

    # If triggers are extremely tight and epsilon collapses range, fall back to midpoint
    if not (low < last_price < high):
        last_price = mid

    return float(last_price)


def place_orders(kite, intents: List[OrderIntent], linker=None, live: bool = True):
    """
    Places BUY orders immediately.
    SELL orders:
      - If GTT -> placed ONLY via place_gtt
      - If non-GTT -> queued via linker or placed directly
    """

    results = []

    for intent in intents:
        # -----------------------------
        # BUY ORDERS (place immediately, normal OR GTT)
        # -----------------------------
        if intent.txn_type == "BUY":
            if intent.gtt == "YES":
                if intent.gtt_type == "SINGLE":
                    trigger = float(intent.gtt_trigger)
                    price = float(intent.gtt_limit)
                    print(f"[PLACEMENT] GTT SINGLE BUY: {intent.symbol} qty={intent.qty} trigger={trigger} limit={price}")
                    last_price = _resolve_last_price(kite, intent, trigger)
                    response = kite.place_gtt(
                        trigger_type=kite.GTT_TYPE_SINGLE,
                        tradingsymbol=intent.symbol,
                        exchange=intent.exchange,
                        trigger_values=[trigger],
                        last_price=last_price,
                        orders=[{
                            "transaction_type": "BUY",
                            "quantity": intent.qty,
                            "order_type": "LIMIT",
                            "price": price,
                            "product": intent.product,
                        }],
                    )
                    # Safe extraction of GTT ID from response (supports id/trigger_id/data.id)
                    order_id = (
                        response.get("id")
                        or response.get("trigger_id")
                        or response.get("data", {}).get("id")
                        or response.get("data", {}).get("trigger_id")
                    )
                    if not order_id:
                        raise ValueError(f"GTT placement failed: no ID in response {response}")
                    print(f"[PLACEMENT] GTT placed: {order_id}")
                    results.append({
                        "order_id": order_id,
                        "symbol": intent.symbol,
                        "txn_type": "BUY",
                        "qty": intent.qty,
                        "status": "gtt_placed",
                        "trigger": trigger,
                        "limit": price,
                    })
                elif intent.gtt_type == "OCO":
                    trig1 = float(intent.gtt_trigger_1)
                    price1 = float(intent.gtt_limit_1)
                    trig2 = float(intent.gtt_trigger_2)
                    price2 = float(intent.gtt_limit_2)
                    # Ensure triggers are passed in ascending order with matching orders
                    legs = [
                        (trig1, {
                            "transaction_type": "BUY",
                            "quantity": intent.qty,
                            "order_type": "LIMIT",
                            "price": price1,
                            "product": intent.product,
                        }),
                        (trig2, {
                            "transaction_type": "BUY",
                            "quantity": intent.qty,
                            "order_type": "LIMIT",
                            "price": price2,
                            "product": intent.product,
                        }),
                    ]
                    legs.sort(key=lambda x: float(x[0]))
                    trigger_values = [float(legs[0][0]), float(legs[1][0])]
                    orders_payload = [legs[0][1], legs[1][1]]
                    last_price = _resolve_last_price_for_oco(kite, intent, trigger_values[0], trigger_values[1])
                    response = kite.place_gtt(
                        trigger_type=kite.GTT_TYPE_OCO,
                        tradingsymbol=intent.symbol,
                        exchange=intent.exchange,
                        trigger_values=trigger_values,
                        last_price=last_price,
                        orders=orders_payload,
                    )
                    # Safe extraction of GTT ID from response (supports id/trigger_id/data.id)
                    order_id = (
                        response.get("id")
                        or response.get("trigger_id")
                        or response.get("data", {}).get("id")
                        or response.get("data", {}).get("trigger_id")
                    )
                    if not order_id:
                        raise ValueError(f"GTT placement failed: no ID in response {response}")
                    results.append({
                        "order_id": order_id,
                        "symbol": intent.symbol,
                        "txn_type": "BUY",
                        "qty": intent.qty,
                        "status": "gtt_placed",
                        "trigger_1": trig1,
                        "limit_1": price1,
                        "trigger_2": trig2,
                        "limit_2": price2,
                    })
                else:
                    raise ValueError(f"Unsupported GTT type for BUY: {intent.gtt_type}")
                # Register GTT BUY with linker if tagged (not exit)
                if linker and intent.tag and intent.tag.startswith("link:"):
                    linker.register_gtt_buy(str(order_id), intent)  # Convert to string for consistency
                    print(f"[LINKER] Registered GTT BUY: {order_id} → {intent.symbol} tag={intent.tag}")
            else:
                payload = intent.to_kite_payload()
                order_id = kite.place_order(**payload)
                results.append({
                    "order_id": order_id,
                    "symbol": intent.symbol,
                    "txn_type": "BUY",
                    "qty": intent.qty,
                    "status": "placed",
                })
                # Register normal BUY with linker if tagged (not exit)
                if linker and intent.tag and intent.tag.startswith("link:"):
                    linker.register_buy(order_id, intent)
            continue

        # -----------------------------
        # SELL ORDERS — QUEUE IF LINKED; EXIT ORDERS PLACE IMMEDIATELY
        # -----------------------------
        if intent.txn_type == "SELL":
            # Exit orders bypass queueing
            if intent.tag == "exit":
                payload = intent.to_kite_payload()
                order_id = kite.place_order(**payload)
                results.append({
                    "order_id": order_id,
                    "symbol": intent.symbol,
                    "txn_type": "SELL",
                    "qty": intent.qty,
                    "status": "placed",
                })
                continue
            
            # Regular SELLs must have link tag and will be queued
            if not (linker and intent.tag and intent.tag.startswith("link:")):
                raise ValueError("SELL orders must have tag=link:<group> and will be queued")
            linker.queue_sell(intent)
            print(f"[LINKER] Queued SELL: {intent.symbol} qty={intent.qty} gtt={intent.gtt} gtt_type={intent.gtt_type}")
            results.append({
                "order_id": None,
                "symbol": intent.symbol,
                "txn_type": "SELL",
                "qty": intent.qty,
                "status": "queued",
                "gtt": intent.gtt,
                "gtt_type": intent.gtt_type,
            })
            continue

        # -----------------------------
        raise ValueError(f"Unknown txn_type: {intent.txn_type}")

    return results

def place_released_sells(kite, sells: List[OrderIntent], live: bool = True):
    results = []
    for intent in sells:
        if intent.txn_type != "SELL":
            continue
        if intent.gtt == "YES":
            if intent.gtt_type == "SINGLE":
                trigger = float(intent.gtt_trigger)
                price = float(intent.gtt_limit)
                last_price = _resolve_last_price(kite, intent, trigger)
                response = kite.place_gtt(
                    trigger_type=kite.GTT_TYPE_SINGLE,
                    tradingsymbol=intent.symbol,
                    exchange=intent.exchange,
                    trigger_values=[trigger],
                    last_price=last_price,
                    orders=[{
                        "transaction_type": "SELL",
                        "quantity": intent.qty,
                        "order_type": "LIMIT",
                        "price": price,
                        "product": intent.product,
                    }],
                )
                gtt_id = (
                    response.get("id")
                    or response.get("trigger_id")
                    or response.get("data", {}).get("id")
                    or response.get("data", {}).get("trigger_id")
                )
                if not gtt_id:
                    raise ValueError(f"GTT placement failed: no ID in response {response}")
                results.append({
                    "order_id": gtt_id,
                    "symbol": intent.symbol,
                    "txn_type": "SELL",
                    "qty": intent.qty,
                    "status": "gtt_placed",
                })
            elif intent.gtt_type == "OCO":
                trig1 = float(intent.gtt_trigger_1)
                price1 = float(intent.gtt_limit_1)
                trig2 = float(intent.gtt_trigger_2)
                price2 = float(intent.gtt_limit_2)
                # Ensure triggers are passed in ascending order with matching orders
                legs = [
                    (trig1, {
                        "transaction_type": "SELL",
                        "quantity": intent.qty,
                        "order_type": "LIMIT",
                        "price": price1,
                        "product": intent.product,
                    }),
                    (trig2, {
                        "transaction_type": "SELL",
                        "quantity": intent.qty,
                        "order_type": "LIMIT",
                        "price": price2,
                        "product": intent.product,
                    }),
                ]
                legs.sort(key=lambda x: float(x[0]))
                trigger_values = [float(legs[0][0]), float(legs[1][0])]
                orders_payload = [legs[0][1], legs[1][1]]
                last_price = _resolve_last_price_for_oco(kite, intent, trigger_values[0], trigger_values[1])
                print(
                    f"[PLACEMENT] GTT OCO SELL: {intent.symbol} qty={intent.qty} "
                    f"triggers={trigger_values} last_price={last_price}"
                )
                response = kite.place_gtt(
                    trigger_type=kite.GTT_TYPE_OCO,
                    tradingsymbol=intent.symbol,
                    exchange=intent.exchange,
                    trigger_values=trigger_values,
                    last_price=last_price,
                    orders=orders_payload,
                )
                gtt_id = (
                    response.get("id")
                    or response.get("trigger_id")
                    or response.get("data", {}).get("id")
                    or response.get("data", {}).get("trigger_id")
                )
                if not gtt_id:
                    raise ValueError(f"GTT placement failed: no ID in response {response}")
                results.append({
                    "order_id": gtt_id,
                    "symbol": intent.symbol,
                    "txn_type": "SELL",
                    "qty": intent.qty,
                    "status": "gtt_placed",
                })
            else:
                raise ValueError("Unsupported GTT type for SELL")
        else:
            payload = intent.to_kite_payload()
            order_id = kite.place_order(**payload)
            results.append({
                "order_id": order_id,
                "symbol": intent.symbol,
                "txn_type": "SELL",
                "qty": intent.qty,
                "status": "placed",
            })
    return results
