# models.py
from __future__ import annotations
from pydantic import BaseModel, field_validator
from typing import Optional


class OrderIntent(BaseModel):
    # Core trading fields
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

    # allow "exit" and "link:<group>"
    tag: Optional[str] = None

    # GTT fields
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
        """
        Allowed:
        - None
        - "exit"
        - "link:<group>"
        """
        if v is None:
            return None

        if not isinstance(v, str):
            return None

        v = v.strip()
        if v == "":
            return None

        lo = v.lower()

        if lo == "exit":
            return "exit"

        if lo.startswith("link:"):
            group = lo.split(":", 1)[1].strip()
            if not group:
                raise ValueError("tag requires group after 'link:'")
            return f"link:{group}"

        raise ValueError("tag must be 'exit' or 'link:<group>'")
