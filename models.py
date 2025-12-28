# models.py
from __future__ import annotations
from pydantic import BaseModel, field_validator
from typing import Optional


class OrderIntent(BaseModel):
    exchange: str
    symbol: str
    txn_type: str       # BUY / SELL
    qty: int

    order_type: str     # MARKET / LIMIT / SL / SL-M
    price: Optional[float]
    trigger_price: Optional[float]

    product: str        # NRML
    validity: str       # DAY
    variety: str        # regular
    disclosed_qty: int = 0

    tag: Optional[str] = None

    gtt: str = "NO"
    gtt_type: Optional[str] = None

    gtt_trigger: Optional[float] = None
    gtt_limit: Optional[float] = None

    gtt_trigger_1: Optional[float] = None
    gtt_limit_1: Optional[float] = None

    gtt_trigger_2: Optional[float] = None
    gtt_limit_2: Optional[float] = None

    # BUY queue trigger: if set, queue BUY instead of placing immediately
    # trigger_price is reused as the queue trigger
    tolerance: Optional[float] = None  # ±tolerance around trigger_price to place
    
    # Row tracking (for UI error reporting)
    source_row: Optional[int] = None

    # ---------------------------
    # VALIDATORS
    # ---------------------------
    @field_validator("tag")
    def validate_tag(cls, v):
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if v.lower() == "exit":
            return "exit"
        if v.lower().startswith("link:"):
            group = v.split(":", 1)[1].strip()
            if not group:
                raise ValueError("tag requires group after 'link:'")
            return f"link:{group}"
        raise ValueError("tag must be 'exit' or 'link:<group>'")

    # ---------------------------
    # PAYLOAD BUILDER
    # ---------------------------
    def to_kite_payload(self):
        """
        Build payload ONLY for NON-GTT orders.
        GTT orders must be placed via place_gtt().
        """

        # 🚫 HARD STOP: GTT never uses place_order
        if self.gtt == "YES":
            return None

        payload = {
            "exchange": self.exchange,
            "tradingsymbol": self.symbol,
            "transaction_type": self.txn_type,
            "quantity": self.qty,
            "product": self.product,
            "validity": self.validity,
            "variety": self.variety,
        }

        if self.order_type == "MARKET":
            payload["order_type"] = "MARKET"

        elif self.order_type == "LIMIT":
            payload["order_type"] = "LIMIT"
            if self.price is None:
                raise ValueError("LIMIT order requires price")
            payload["price"] = float(self.price)

        elif self.order_type in ("SL", "SL-M"):
            payload["order_type"] = self.order_type
            if self.trigger_price is None:
                raise ValueError("SL / SL-M requires trigger_price")
            payload["trigger_price"] = float(self.trigger_price)
            if self.order_type == "SL":
                if self.price is None:
                    raise ValueError("SL order requires price")
                payload["price"] = float(self.price)

        else:
            raise ValueError(f"Unsupported order_type: {self.order_type}")

        if self.disclosed_qty:
            payload["disclosed_quantity"] = self.disclosed_qty

        return payload
