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

    gtt: Optional[str] = None
    gtt_type: Optional[str] = None
    limit_price: Optional[float] = None
    trigger_price_1: Optional[float] = None
    limit_price_1: Optional[float] = None
    trigger_price_2: Optional[float] = None
    limit_price_2: Optional[float] = None

    @field_validator("symbol", "exchange")
    @classmethod
    def _upper_req(cls, v: str) -> str:
        s = (v or "").strip().upper()
        if not s:
            raise ValueError("required")
        return s

    @field_validator("txn_type")
    @classmethod
    def _txn(cls, v: str) -> str:
        u = (v or "").strip().upper()
        if u not in {"BUY", "SELL"}:
            raise ValueError("txn_type must be BUY/SELL")
        return u

    @field_validator("qty")
    @classmethod
    def _qty(cls, v: int) -> int:
        q = int(v)
        if q < 1:
            raise ValueError("qty >= 1")
        return q

    @field_validator("order_type")
    @classmethod
    def _ot(cls, v: str) -> str:
        u = (v or "").strip().upper()
        if u not in _ALLOWED_ORDER_TYPES:
            raise ValueError("order_type must be MARKET/LIMIT/SL/SL-M")
        return u

    @field_validator("product", "validity", "variety", mode="before")
    @classmethod
    def _opt_u(cls, v):
        return None if v is None else str(v).strip().upper()

    @field_validator("gtt", "gtt_type", mode="before")
    @classmethod
    def _opt_g(cls, v):
        return None if v is None else str(v).strip().upper()

    @model_validator(mode="after")
    def _cross(self):
        # Force NRML everywhere
        self.product = "NRML"
        self.validity = (self.validity or "DAY").upper()
        self.variety = (self.variety or "regular").upper()

        is_gtt = (self.gtt or "").upper() == "YES"
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
            self.gtt_type = (self.gtt_type or "SINGLE").upper()
            self.price = None
            if self.gtt_type == "SINGLE":
                if self.trigger_price is None or self.limit_price is None:
                    raise ValueError("GTT SINGLE requires trigger_price & limit_price")
            elif self.gtt_type == "OCO":
                need = [self.trigger_price_1, self.limit_price_1, self.trigger_price_2, self.limit_price_2]
                if any(v is None for v in need):
                    raise ValueError("GTT OCO requires both legs")
                if float(self.trigger_price_1) == float(self.trigger_price_2):
                    raise ValueError("OCO trigger prices must differ")
                self.trigger_price = None
            else:
                raise ValueError("gtt_type must be SINGLE/OCO")
        return self
