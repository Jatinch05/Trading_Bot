# services/orders/splitter.py
# Splits incoming intents into:
# - regular orders
# - gtt single
# - gtt oco
# - linked sells (tag=link:X), handled via WS linker

from typing import List, Dict
from models import OrderIntent


def split_intents(intents: List[OrderIntent]) -> Dict[str, List[OrderIntent]]:
    """
    Returns:
        {
            "regular": [...],
            "gtt_single": [...],
            "gtt_oco": [...],
            "linked_sells": [...]
        }
    """

    buckets = {
        "regular": [],
        "gtt_single": [],
        "gtt_oco": [],
        "linked_sells": [],
    }

    for o in intents:

        # ------------------------------
        # WS-linked SELLs (tag=link:X)
        # ------------------------------
        if o.txn_type == "SELL" and o.tag and o.tag.startswith("link:"):
            buckets["linked_sells"].append(o)
            continue

        # ------------------------------
        # GTT SINGLE
        # ------------------------------
        if o.gtt == "YES" and o.gtt_type == "SINGLE":
            buckets["gtt_single"].append(o)
            continue

        # ------------------------------
        # GTT OCO
        # ------------------------------
        if o.gtt == "YES" and o.gtt_type == "OCO":
            buckets["gtt_oco"].append(o)
            continue

        # ------------------------------
        # Regular Orders
        # ------------------------------
        buckets["regular"].append(o)

    return buckets
