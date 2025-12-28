# services/orders/placement.py

from typing import List
from models import OrderIntent


def _get_ltp(kite, intent: OrderIntent) -> float | None:
    key = f"{intent.exchange}:{intent.symbol}"
    try:
        data = kite.ltp([key])
        if isinstance(data, dict) and key in data:
            lp = data[key].get("last_price")
            if lp is not None:
                return float(lp)
    except Exception:
        return None
    return None


def _resolve_last_price_single(kite, intent: OrderIntent, trigger: float) -> float:
    """Use raw live LTP when available; otherwise use trigger.

    Per user request, do not adjust/nudge last_price before calling the broker.
    """

    ltp = _get_ltp(kite, intent)
    if ltp is not None:
        return float(ltp)
    return float(trigger)


def _get_ltp(kite, intent: OrderIntent) -> float | None:
    key = f"{intent.exchange}:{intent.symbol}"
    try:
        data = kite.ltp([key])
        if isinstance(data, dict) and key in data:
            lp = data[key].get("last_price")
            if lp is not None:
                return float(lp)
    except Exception:
        return None
    return None


def _resolve_last_price_for_oco(kite, intent: OrderIntent, trig_a: float, trig_b: float) -> float:
    """Use raw live LTP when available; otherwise midpoint.

    Per user request, no clamping/nudging before sending to broker.
    """

    ltp = _get_ltp(kite, intent)
    if ltp is not None:
        return float(ltp)
    low = float(min(trig_a, trig_b))
    high = float(max(trig_a, trig_b))
    return (low + high) / 2.0


def place_orders(kite, intents: List[OrderIntent], linker=None, live: bool = True):
    """
    Places BUY orders immediately or queues them if tolerance is set.
    SELL orders:
      - If GTT -> placed ONLY via place_gtt
      - If non-GTT -> queued via linker or placed directly
    """

    results = []

    for intent in intents:
        # Check if this BUY should be queued (tolerance is set)
        if intent.txn_type == "BUY":
            tolerance = getattr(intent, "tolerance", None)
            if tolerance is not None and linker is not None:
                # Queue this BUY to be placed when price hits trigger ± tolerance
                trigger = intent.trigger_price if intent.trigger_price is not None else intent.price
                if trigger is None:
                    raise ValueError(f"BUY queue requires trigger_price or price for {intent.symbol}")
                linker.queue_buy(intent, trigger, tolerance)
                results.append({
                    "order_id": None,
                    "symbol": intent.symbol,
                    "txn_type": "BUY",
                    "qty": intent.qty,
                    "status": "queued",
                    "trigger": trigger,
                    "tolerance": tolerance,
                })
                continue

        # Regular placement logic (immediate BUY or GTT or SELL)
        # ----- 
        # BUY ORDERS (place immediately, normal OR GTT)
        # -----
        if intent.txn_type == "BUY":
            if intent.gtt == "YES":
                if intent.gtt_type == "SINGLE":
                    trigger = float(intent.gtt_trigger)
                    price = float(intent.gtt_limit)
                    last_price = _resolve_last_price_single(kite, intent, trigger)
                    print(
                        f"[PLACEMENT] GTT SINGLE BUY: {intent.symbol} qty={intent.qty} "
                        f"trigger={trigger} limit={price} last_price={last_price}"
                    )
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
                last_price = _resolve_last_price_single(kite, intent, trigger)
                print(
                    f"[PLACEMENT] GTT SINGLE SELL: {intent.symbol} qty={intent.qty} "
                    f"trigger={trigger} limit={price} last_price={last_price}"
                )
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
