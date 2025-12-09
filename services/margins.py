import pandas as pd
from typing import List
from models import OrderIntent

def estimate_notional(intents: List[OrderIntent]) -> pd.DataFrame:
    # Simple demo estimator — replace with Kite basket margin API later
    out = []
    for oi in intents:
        notional = (oi.price or 0) * oi.qty
        out.append({
            "tradingsymbol": oi.tradingsymbol,
            "side": oi.txn_type,
            "approx_notional": notional
        })
    return pd.DataFrame(out)
