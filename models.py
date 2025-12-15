# models.py
from __future__ import annotations
from pydantic import BaseModel, field_validator
from typing import Optional


class OrderIntent(BaseModel):
    # ===============================
    # Core trading fields
    # ===============================
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

    # ===============================
    # GTT fields (explicit only)
    # ===============================
    gtt: str = "NO"
    gtt_type: Optional[str] = None
    gtt_trigger: Optional[float] = None
    gtt_limit: Optional[float] = None
    gtt_trigger_1: Optional[float] = None
    gtt_limit_1: Optional[float] = None
    gtt_trigger_2: Optional[float] = None
    gtt_limit_2: Optional[float] = None

    # ===============================
    # Validators
    # ===============================
    @field_validator("tag")
    def validate_tag(cls, v):
        if not v:
            return None
        v = v.strip().lower()
        if v == "exit":
            return "exit"
        if v.startswith("link:") and v.split(":", 1)[1].strip():
            return v
        raise ValueError("tag must be 'exit' or 'link:<group>'")

    # ===============================
    # Kite payload builder
    # ===============================
    def to_kite_payload(self) -> Optional[dict]:
        """
        Returns:
        - dict → place immediately via kite.place_order
        - None → defer (linked / OCO SELL without price)
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

        # -------------------------------
        # Order-type normalization
        # -------------------------------
        if self.order_type == "MARKET":
            pass

        elif self.order_type == "LIMIT":
            if self.price is None:
                # KEY FIX: SELL LIMIT without price is deferred (OCO / linked)
                if self.txn_type == "SELL":
                    return None
                raise ValueError("BUY LIMIT requires price")
            payload["price"] = self._round_price(self.price)

        elif self.order_type == "SL-M":
            if self.trigger_price is None:
                raise ValueError("SL-M requires trigger_price")
            payload["trigger_price"] = self._round_price(self.trigger_price)

        elif self.order_type == "SL":
            if self.price is None or self.trigger_price is None:
                raise ValueError("SL requires price and trigger_price")
            payload["price"] = self._round_price(self.price)
            payload["trigger_price"] = self._round_price(self.trigger_price)

        else:
            raise ValueError(f"Unsupported order_type: {self.order_type}")

        # -------------------------------
        # Optional fields
        # -------------------------------
        if self.tag:
            payload["tag"] = self.tag

        if self.disclosed_qty and self.disclosed_qty > 0:
            if self.disclosed_qty >= self.qty:
                raise ValueError("disclosed_qty must be < qty")
            payload["disclosed_quantity"] = self.disclosed_qty

        # -------------------------------
        # GTT stripping (CRITICAL)
        # -------------------------------
        if self.gtt != "YES":
            # ensure this is NEVER misrouted as GTT
            pass

        return payload

    # ===============================
    # Helpers
    # ===============================
    @staticmethod
    def _round_price(p: float) -> float:
        # safe default tick size
        return round(p / 0.05) * 0.05
