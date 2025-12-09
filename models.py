# models.py
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

_ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}

class OrderIntent(BaseModel):
    # Core order fields
    symbol: str
    exchange: str
    txn_type: str
    qty: int

    order_type: str
    price: Optional[float] = None
    trigger_price: Optional[float] = None

    product: Optional[str] = None
    validity: Optional[str] = None
    variety: Optional[str] = None
    disclosed_qty: Optional[int] = None
    tag: Optional[str] = None

    # GTT extensions (passthrough; used by services.gtt)
    gtt: Optional[str] = None            # "YES" or ""
    gtt_type: Optional[str] = None       # "SINGLE" or "OCO"
    limit_price: Optional[float] = None  # SINGLE
    trigger_price_1: Optional[float] = None  # OCO
    limit_price_1: Optional[float] = None    # OCO
    trigger_price_2: Optional[float] = None  # OCO
    limit_price_2: Optional[float] = None    # OCO

    # -------------------------
    # Field-level validators
    # -------------------------
    @field_validator("txn_type")
    @classmethod
    def _txn_upper_and_check(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in {"BUY", "SELL"}:
            raise ValueError("txn_type must be BUY or SELL")
        return v

    @field_validator("order_type")
    @classmethod
    def _order_type_check(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in _ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")
        return v

    @field_validator("symbol", "exchange", "product", "validity", "variety", mode="before")
    @classmethod
    def _strip_upper_optional(cls, v):
        if v is None:
            return v
        s = str(v).strip()
        # Keep product/validity/variety case-insensitive; normalize to upper for exchange/symbol
        return s.upper() if s and (cls.__name__ and True) else s

    # -------------------------
    # Model-level validator
    # -------------------------
    @model_validator(mode="after")
    def _cross_field_rules(self) -> "OrderIntent":
        """
        Enforce cross-field rules, with an explicit exception for GTT rows:
        - If gtt == "YES": allow trigger_price (SINGLE) and OCO fields regardless of order_type.
        - If not GTT:
            * MARKET: trigger_price must be None
            * LIMIT/SL/SL-M: price numeric; SL/SL-M may require trigger_price by your upstream logic
        """
        is_gtt = (self.gtt or "").strip().upper() == "YES"
        ot = (self.order_type or "").upper()

        if not is_gtt:
            # Non-GTT orders
            if ot == "MARKET":
                if self.trigger_price is not None:
                    raise ValueError("MARKET must not include trigger_price")
                # Ignore any provided price for MARKET
                self.price = None
            elif ot in {"LIMIT", "SL", "SL-M"}:
                # price must be provided for LIMIT/SL/SL-M (your upstream may already enforce)
                # If price is missing or not numeric, Pydantic will surface earlier; enforce presence here.
                if self.price is None:
                    raise ValueError(f"{ot} requires price")
        else:
            # GTT orders: permit trigger fields irrespective of order_type.
            # Normalize gtt_type
            gtt_type = (self.gtt_type or "SINGLE").strip().upper()
            self.gtt_type = gtt_type

            if gtt_type == "SINGLE":
                # Need trigger_price + limit_price
                if self.trigger_price is None:
                    raise ValueError("GTT SINGLE requires trigger_price")
                if self.limit_price is None:
                    raise ValueError("GTT SINGLE requires limit_price")
                # price is irrelevant for GTT
                self.price = None

            elif gtt_type == "OCO":
                # Need both legs
                if self.trigger_price_1 is None or self.limit_price_1 is None \
                   or self.trigger_price_2 is None or self.limit_price_2 is None:
                    raise ValueError("GTT OCO requires trigger_price_1/limit_price_1 and trigger_price_2/limit_price_2")
                if float(self.trigger_price_1) == float(self.trigger_price_2):
                    raise ValueError("OCO trigger prices must differ")
                # price/trigger_price (single) irrelevant for OCO
                self.price = None
                self.trigger_price = None
            else:
                raise ValueError("gtt_type must be SINGLE or OCO")

        return self
