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


def _placed_sells_dir():
    from pathlib import Path

    d = Path(__file__).resolve().parents[2] / ".runtime" / "placed_sells"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _try_acquire_sell_inflight(intent) -> tuple[bool, str, object]:
    """Acquire an in-flight lock for a SELL intent.

    Returns:
        (ok, reason, ctx)

    This is a two-phase idempotency scheme:
    - Acquire `<hash>.inprogress` BEFORE calling Kite APIs, to avoid two Streamlit
      sessions placing the same SELL concurrently.
    - Only after successful placement, atomically promote it to `<hash>.done`.

    This fixes the bug where we created a permanent lock BEFORE placement; if the
    app refreshed/crashed or placement threw, the SELL would be incorrectly
    suppressed on retry.
    """
    import os
    import hashlib
    import datetime as _dt

    if getattr(intent, "txn_type", None) != "SELL":
        return False, "not_sell", None

    sig = _sell_signature(intent)
    h = hashlib.sha256(sig.encode("utf-8")).hexdigest()
    lock_dir = _placed_sells_dir()
    done = lock_dir / f"{h}.done"
    inflight = lock_dir / f"{h}.inprogress"

    done_ttl = 12 * 60 * 60        # 12h (suppress duplicates across reruns)
    inflight_ttl = 5 * 60          # 5m (allow retry if a session died mid-place)

    def _is_stale(path, ttl):
        try:
            now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
            age = now_ts - path.stat().st_mtime
            return age > ttl
        except Exception:
            return False

    # If we already have a completed marker and it's fresh: skip.
    if done.exists() and not _is_stale(done, done_ttl):
        return False, "done", None
    # If done is stale: allow retry.
    if done.exists() and _is_stale(done, done_ttl):
        try:
            done.unlink(missing_ok=True)
        except Exception:
            return False, "done_locked", None

    # If another session is currently placing it: skip (unless stale).
    if inflight.exists() and not _is_stale(inflight, inflight_ttl):
        return False, "inflight", None
    if inflight.exists() and _is_stale(inflight, inflight_ttl):
        try:
            inflight.unlink(missing_ok=True)
        except Exception:
            return False, "inflight_locked", None

    try:
        with open(inflight, "x", encoding="utf-8") as f:
            f.write(sig)
        return True, "acquired", {"sig": sig, "done": done, "inflight": inflight, "os": os}
    except FileExistsError:
        return False, "inflight", None
    except Exception:
        return False, "fs_error", None


def _promote_sell_inflight(ctx, *, placed_result) -> None:
    import json

    inflight = ctx["inflight"]
    done = ctx["done"]
    os_mod = ctx["os"]

    try:
        # enrich the marker with placement info
        payload = {"sig": ctx.get("sig"), "result": placed_result}
        inflight.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    except Exception:
        # best-effort only
        pass

    try:
        os_mod.replace(str(inflight), str(done))
    except Exception:
        # If atomic replace fails, at least try to create the done file
        try:
            done.write_text(ctx.get("sig", ""))
        except Exception:
            pass
        try:
            inflight.unlink(missing_ok=True)
        except Exception:
            pass


def _release_sell_inflight(ctx) -> None:
    try:
        ctx["inflight"].unlink(missing_ok=True)
    except Exception:
        pass


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
    sells = sells or []
    requested = len(sells)

    placed_results = []
    skipped = 0

    for intent in sells:
        ok, reason, ctx = _try_acquire_sell_inflight(intent)
        if not ok:
            if reason in ("done", "inflight"):
                skipped += 1
            continue

        try:
            # Place one-by-one so we can accurately commit idempotency per SELL.
            res = place_released_sells(kite=kite, sells=[intent], live=live)
            placed_results.extend(res or [])
            _promote_sell_inflight(ctx, placed_result=(res[0] if res else None))
        except Exception as e:
            print(f"[PIPELINE] Released SELL placement failed; will allow retry: {e}")
            _release_sell_inflight(ctx)

    if skipped:
        print(f"[PIPELINE] Idempotency skipped {skipped} duplicate/in-flight released SELL(s)")
    print(f"[PIPELINE] Released SELLs requested={requested} placed={len(placed_results)}")
    return placed_results
