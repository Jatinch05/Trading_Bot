# models.py — NRML-only

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

_ALLOWED_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}


class OrderIntent(BaseModel):
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

    # GTT extensions
    gtt: Optional[str] = None            # "YES" or ""
    gtt_type: Optional[str] = None       # "SINGLE" or "OCO"
    limit_price: Optional[float] = None  # SINGLE
    trigger_price_1: Optional[float] = None  # OCO
    limit_price_1: Optional[float] = None    # OCO
    trigger_price_2: Optional[float] = None  # OCO
    limit_price_2: Optional[float] = None    # OCO

    @field_validator("symbol")
    @classmethod
    def _symbol_norm(cls, v: str) -> str:
        s = (v or "").strip().upper()
        if not s:
            raise ValueError("symbol is required")
        return s

    @field_validator("exchange")
    @classmethod
    def _exchange_norm(cls, v: str) -> str:
        s = (v or "").strip().upper()
        if not s:
            raise ValueError("exchange is required")
        return s

    @field_validator("txn_type")
    @classmethod
    def _txn_upper_and_check(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v not in {"BUY", "SELL"}:
            raise ValueError("txn_type must be BUY or SELL")
        return v

    @field_validator("qty")
    @classmethod
    def _qty_pos_int(cls, v: int) -> int:
        try:
            q = int(v)
        except Exception:
            raise ValueError("qty must be an integer")
        if q < 1:
            raise ValueError("qty must be >= 1")
        return q

    @field_validator("order_type")
    @classmethod
    def _order_type_check(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v not in _ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")
        return v

    @field_validator("product", "validity", "variety", mode="before")
    @classmethod
    def _opt_strip_upper(cls, v):
        if v is None:
            return v
        return str(v).strip().upper()

    @field_validator("gtt", "gtt_type", mode="before")
    @classmethod
    def _opt_strip_upper_flags(cls, v):
        if v is None:
            return v
        return str(v).strip().upper()

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "OrderIntent":
        # Force NRML everywhere
        self.product = "NRML"
        self.validity = (self.validity or "DAY").upper()
        self.variety = (self.variety or "regular").upper()

        is_gtt = (self.gtt or "").strip().upper() == "YES"
        ot = (self.order_type or "").upper()

        if not is_gtt:
            if ot == "MARKET":
                if self.trigger_price is not None:
                    raise ValueError("MARKET must not include trigger_price")
                self.price = None
            elif ot in {"LIMIT", "SL", "SL-M"}:
                if self.price is None:
                    raise ValueError(f"{ot} requires price")
                if ot in {"SL", "SL-M"} and self.trigger_price is None:
                    raise ValueError(f"{ot} requires trigger_price")
        else:
            self.gtt = "YES"
            gtt_type = (self.gtt_type or "SINGLE").strip().upper()
            self.gtt_type = gtt_type
            self.price = None  # irrelevant for GTT

            if gtt_type == "SINGLE":
                if self.trigger_price is None:
                    raise ValueError("GTT SINGLE requires trigger_price")
                if self.limit_price is None:
                    raise ValueError("GTT SINGLE requires limit_price")
            elif gtt_type == "OCO":
                if self.trigger_price_1 is None or self.limit_price_1 is None \
                   or self.trigger_price_2 is None or self.limit_price_2 is None:
                    raise ValueError("GTT OCO requires trigger_price_1/limit_price_1 and trigger_price_2/limit_price_2")
                if float(self.trigger_price_1) == float(self.trigger_price_2):
                    raise ValueError("OCO trigger prices must differ")
                self.trigger_price = None
            else:
                raise ValueError("gtt_type must be SINGLE or OCO")
        return self

    # compatibility aliases for any older code
    @property
    def tradingsymbol(self) -> str:
        return self.symbol

    @property
    def transaction_type(self) -> str:
        return self.txn_type

    @property
    def quantity(self) -> int:
        return self.qty
