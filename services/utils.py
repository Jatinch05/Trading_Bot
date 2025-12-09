import hashlib

def align_to_tick(value: float | None, tick: float) -> float | None:
    if value is None: return None
    # round to nearest tick, keep two decimals
    rounded = round(round(value / tick) * tick, 2)
    return rounded

def row_signature(payload: dict) -> str:
    s = repr(sorted(payload.items()))
    return hashlib.sha256(s.encode()).hexdigest()
