# services/orders/placement.py
from kiteconnect import KiteConnect

def place_regular_order(kite, intent):
    payload = {
        "variety": "regular",
        "exchange": intent.exchange,
        "tradingsymbol": intent.symbol,
        "transaction_type": intent.txn_type,
        "quantity": intent.qty,
        "order_type": intent.order_type,
        "product": intent.product,
        "validity": intent.validity,
    }

    if intent.order_type == "LIMIT":
        payload["price"] = intent.price

    if intent.order_type in ("SL", "SL-M"):
        payload["trigger_price"] = intent.trigger_price
        if intent.order_type == "SL":
            payload["price"] = intent.price

    return kite.place_order(**payload)


def place_gtt_single(kite, intent):
    ltp = kite.ltp(f"{intent.exchange}:{intent.symbol}")[
        f"{intent.exchange}:{intent.symbol}"
    ]["last_price"]

    return kite.place_gtt(
        trigger_type="single",
        tradingsymbol=intent.symbol,
        exchange=intent.exchange,
        trigger_values=[intent.gtt_trigger],
        last_price=ltp,
        orders=[{
            "transaction_type": intent.txn_type,
            "quantity": intent.qty,
            "order_type": "LIMIT",
            "product": intent.product,
            "price": intent.gtt_limit,
        }],
    )


def place_gtt_oco(kite, intent):
    ltp = kite.ltp(f"{intent.exchange}:{intent.symbol}")[
        f"{intent.exchange}:{intent.symbol}"
    ]["last_price"]

    return kite.place_gtt(
        trigger_type="two-leg",
        tradingsymbol=intent.symbol,
        exchange=intent.exchange,
        trigger_values=[intent.gtt_trigger_1, intent.gtt_trigger_2],
        last_price=ltp,
        orders=[
            {
                "transaction_type": intent.txn_type,
                "quantity": intent.qty,
                "order_type": "LIMIT",
                "product": intent.product,
                "price": intent.gtt_limit_1,
            },
            {
                "transaction_type": intent.txn_type,
                "quantity": intent.qty,
                "order_type": "LIMIT",
                "product": intent.product,
                "price": intent.gtt_limit_2,
            },
        ],
    )
