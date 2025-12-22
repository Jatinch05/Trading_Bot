# services/orders/pipeline.py

from services.orders.placement import place_orders, place_released_sells


def _sell_signature(intent) -> str:
    """Stable signature for idempotent SELL placement across reruns/sessions."""
    import json
    import math

    def _canon(v):
        if v is None:
            return None
        # normalize NaN
        try:
            if isinstance(v, float) and math.isnan(v):
                return None
        except Exception:
            pass
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        # normalize numeric-ish
        if isinstance(v, (int, float)):
            return float(v)
        return v

    payload = {
        "exchange": _canon(getattr(intent, "exchange", None)),
        "symbol": _canon(getattr(intent, "symbol", None)),
        "txn_type": _canon(getattr(intent, "txn_type", None)),
        "qty": _canon(getattr(intent, "qty", None)),
        "order_type": _canon(getattr(intent, "order_type", None)),
        "price": _canon(getattr(intent, "price", None)),
        "trigger_price": _canon(getattr(intent, "trigger_price", None)),
        "product": _canon(getattr(intent, "product", None)),
        "validity": _canon(getattr(intent, "validity", None)),
        "variety": _canon(getattr(intent, "variety", None)),
        "disclosed_qty": _canon(getattr(intent, "disclosed_qty", None)),
        "tag": _canon(getattr(intent, "tag", None)),
        "gtt": _canon(getattr(intent, "gtt", None)),
        "gtt_type": _canon(getattr(intent, "gtt_type", None)),
        "gtt_trigger": _canon(getattr(intent, "gtt_trigger", None)),
        "gtt_limit": _canon(getattr(intent, "gtt_limit", None)),
        "gtt_trigger_1": _canon(getattr(intent, "gtt_trigger_1", None)),
        "gtt_limit_1": _canon(getattr(intent, "gtt_limit_1", None)),
        "gtt_trigger_2": _canon(getattr(intent, "gtt_trigger_2", None)),
        "gtt_limit_2": _canon(getattr(intent, "gtt_limit_2", None)),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _filter_already_placed_sells(sells):
    """Best-effort idempotency guard.

    Streamlit refresh can create multiple sessions/background threads.
    If two sessions try to place the same released SELLs, we dedupe using
    atomic lock files on disk.
    """
    import hashlib
    import datetime as _dt
    from pathlib import Path

    # project root: .../services/orders/pipeline.py -> parents[2] is repo root
    lock_dir = Path(__file__).resolve().parents[2] / ".runtime" / "placed_sells"
    lock_dir.mkdir(parents=True, exist_ok=True)

    ttl_seconds = 12 * 60 * 60  # 12h
    out = []
    for intent in sells:
        if getattr(intent, "txn_type", None) != "SELL":
            continue
        sig = _sell_signature(intent)
        h = hashlib.sha256(sig.encode("utf-8")).hexdigest()
        lock_path = lock_dir / f"{h}.lock"

        # If lock exists but is stale, remove it
        if lock_path.exists():
            try:
                now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
                age = now_ts - lock_path.stat().st_mtime
                if age > ttl_seconds:
                    lock_path.unlink(missing_ok=True)
            except Exception:
                # If we can't stat/unlink, treat as locked and skip
                continue

        try:
            # Atomic create; fails if already exists
            with open(lock_path, "x", encoding="utf-8") as f:
                f.write(sig)
            out.append(intent)
        except FileExistsError:
            # Already placed by another session/thread
            continue
        except Exception:
            # On unexpected FS error, do not risk duplicates: skip
            continue
    return out


def execute_bundle(*, intents, kite, linker=None, live=True):
    """
    Executes a bundle of intents.
    - BUYs placed immediately
    - SELLs queued or GTT-placed
    - linker decides WHEN sells are released
    """

    if not live:
        return [
            {
                "order_id": None,
                "symbol": i.symbol,
                "txn_type": i.txn_type,
                "qty": i.qty,
                "status": "dry_run",
            }
            for i in intents
        ]

    return place_orders(
        kite=kite,
        intents=intents,
        linker=linker,
        live=live,
    )


def execute_released_sells(*, sells, kite, live=True):
    """
    Place SELL intents that have been released by the linker.
    This function must NOT re-queue — it only places.
    """
    sells = _filter_already_placed_sells(sells)
    return place_released_sells(kite=kite, sells=sells, live=live)
