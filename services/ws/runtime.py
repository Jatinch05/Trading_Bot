import threading
from typing import Optional, Dict, Any

from services.ws.ws_manager import WSManager
from services.ws.gtt_watcher import GTTWatcher


_lock = threading.Lock()
_ws: Optional[WSManager] = None
_gtt: Optional[GTTWatcher] = None
_token: Optional[str] = None
_api_key: Optional[str] = None


def ensure_workers(*, kite, api_key: Optional[str], access_token: Optional[str], linker) -> Dict[str, Any]:
    """Ensure exactly one WSManager + GTTWatcher are running per Python process.

    Streamlit page refresh / new sessions can rerun the script without stopping
    old daemon threads, leading to duplicated events. This guard centralizes the
    workers and restarts them cleanly when the token changes.
    """
    global _ws, _gtt, _token, _api_key

    with _lock:
        # If credentials aren't ready yet, don't start workers.
        if not api_key or not access_token:
            return {"ws": _ws, "gtt": _gtt, "token": _token}

        # If token changes, restart workers (old token will fail / double-credit)
        if _token and access_token and _token != access_token:
            stop_workers()

        _token = access_token or _token
        _api_key = api_key or _api_key

        # WS
        if _ws is None:
            _ws = WSManager(api_key=_api_key, access_token=_token, linker=linker)
            _ws.start()
        else:
            # If linker instance changed, rebind
            try:
                _ws.linker = linker
            except Exception:
                pass

        # GTT watcher
        if _gtt is None:
            _gtt = GTTWatcher(kite)
            _gtt.bind_linker(linker)
            _gtt.start()
        else:
            try:
                _gtt.kite = kite
                _gtt.bind_linker(linker)
            except Exception:
                pass
            if not getattr(_gtt, "running", False):
                _gtt.start()

        return {"ws": _ws, "gtt": _gtt, "token": _token}


def stop_workers() -> None:
    """Stop and clear process-wide workers."""
    global _ws, _gtt

    with _lock:
        if _gtt is not None:
            try:
                _gtt.stop()
            except Exception:
                pass
            _gtt = None

        if _ws is not None:
            try:
                _ws.stop()
            except Exception:
                pass
            _ws = None


def snapshot_workers() -> Dict[str, Any]:
    with _lock:
        return {
            "token_set": bool(_token),
            "token_suffix": (_token[-6:] if _token else None),
            "ws": (_ws.snapshot() if _ws else None),
            "gtt": (_gtt.snapshot() if _gtt else None),
        }
