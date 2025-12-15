# models.py
from __future__ import annotations
from pydantic import BaseModel, field_validator
from typing import Optional


class OrderIntent(BaseModel):
    exchange: str
    symbol: str
    txn_type: str          # BUY / SELL
    qty: int

    order_type: str        # MARKET / LIMIT / SL / SL-M
    price: Optional[float] = None
    trigger_price: Optional[float] = None

    product: str = "NRML"
    validity: str = "DAY"
    variety: str = "regular"

    disclosed_qty: Optional[int] = None
    tag: Optional[str] = None

    # GTT fields (kept, but not inferred)
    gtt: str = "NO"
    gtt_type: Optional[str] = None
    gtt_trigger: Optional[float] = None
    gtt_limit: Optional[float] = None
    gtt_trigger_1: Optional[float] = None
    gtt_limit_1: Optional[float] = None
    gtt_trigger_2: Optional[float] = None
    gtt_limit_2: Optional[float] = None

    @field_validator("tag")
    def validate_tag(cls, v):
        if not v:
            return None
        v = v.strip().lower()
        if v == "exit":
            return "exit"
        if v.startswith("link:") and v.split(":", 1)[1].strip():
            return v
        return None  # revert to permissive behavior

    def to_kite_payload(self) -> Optional[dict]:
        """
        REVERTED behavior:
        - No inference
        - No hard validation
        - Let Kite decide
        - SELL without price may be deferred
        """

        payload = {
            "exchange": self.exchange,
            "tradingsymbol": self.symbol,
            "transaction_type": self.txn_type,
            "quantity": int(self.qty),
            "order_type": self.order_type,
            "product": self.product,
            "validity": self.validity,
            "variety": self.variety,
        }

        # Optional fields (only if present)
        if self.price is not None:
            payload["price"] = self.price

        if self.trigger_price is not None:
            payload["trigger_price"] = self.trigger_price

        if self.tag:
            payload["tag"] = self.tag

        if self.disclosed_qty:
            payload["disclosed_quantity"] = self.disclosed_qty

        # IMPORTANT:
        # If this is a SELL LIMIT without price → defer (same as before)
        if self.txn_type == "SELL" and self.order_type == "LIMIT" and self.price is None:
            return None

        return payload
