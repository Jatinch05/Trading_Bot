# services/orders/placement.py

from typing import List
from models import OrderIntent


def _resolve_last_price(kite, intent: OrderIntent, fallback: float) -> float:
    """Derive last_price for place_gtt. API rejects trigger_price == last_price.

    Strategy:
    1) Try live quote LTP.
    2) If missing or effectively equal to trigger, nudge by a small epsilon away from trigger
       (BUY nudges up, SELL nudges down).
    """

    last_price = fallback
    quote_key = f"{intent.exchange}:{intent.symbol}"
    try:
        quote = kite.quote(quote_key)
        instrument = quote.get(quote_key, {}) if isinstance(quote, dict) else {}
        candidate = instrument.get("last_price") or instrument.get("last_traded_price")
        if candidate:
            last_price = float(candidate)
    except Exception:
        # If quote fails, fall back to provided value
        last_price = fallback

    # If last_price is missing or too close to trigger, nudge it
    if last_price is None:
        last_price = fallback

    diff = abs(last_price - fallback)
    # Kite requires >0.25% difference; use 0.3% to be safe, minimum 0.5
    threshold_pct = 0.003  # 0.3%
    min_epsilon = 0.5
    
    if diff < abs(fallback) * threshold_pct:
        epsilon = max(abs(fallback) * threshold_pct, min_epsilon)
        direction = 1 if intent.txn_type == "BUY" else -1
        last_price = fallback + direction * epsilon

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
                    last_price = _resolve_last_price(kite, intent, trig1)
                    response = kite.place_gtt(
                        trigger_type=kite.GTT_TYPE_OCO,
                        tradingsymbol=intent.symbol,
                        exchange=intent.exchange,
                        trigger_values=[trig1, trig2],
                        last_price=last_price,
                        orders=[
                            {
                                "transaction_type": "BUY",
                                "quantity": intent.qty,
                                "order_type": "LIMIT",
                                "price": price1,
                                "product": intent.product,
                            },
                            {
                                "transaction_type": "BUY",
                                "quantity": intent.qty,
                                "order_type": "LIMIT",
                                "price": price2,
                                "product": intent.product,
                            },
                        ],
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
                    linker.register_gtt_buy(order_id, intent)
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
                last_price = _resolve_last_price(kite, intent, trig1)
                response = kite.place_gtt(
                    trigger_type=kite.GTT_TYPE_OCO,
                    tradingsymbol=intent.symbol,
                    exchange=intent.exchange,
                    trigger_values=[trig1, trig2],
                    last_price=last_price,
                    orders=[
                        {
                            "transaction_type": "SELL",
                            "quantity": intent.qty,
                            "order_type": "LIMIT",
                            "price": price1,
                            "product": intent.product,
                        },
                        {
                            "transaction_type": "SELL",
                            "quantity": intent.qty,
                            "order_type": "LIMIT",
                            "price": price2,
                            "product": intent.product,
                        },
                    ],
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
