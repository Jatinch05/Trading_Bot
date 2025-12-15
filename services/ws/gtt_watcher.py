# services/ws/gtt_watcher.py

import time
import threading

class GTTWatcher:
    """
    Retained only for observability.
    SELL release does NOT depend on this.
    """
    def __init__(self, kite):
        self.kite = kite
        self.running = False
        self.pending = set()
        self.resolved = {}
        self.interval = 2
        self.thread = None

    def add(self, gtt_id):
        self.pending.add(str(gtt_id))

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            self.run_once()
            time.sleep(self.interval)

    def run_once(self):
        try:
            gtts = self.kite.get_gtts()
            for gtt in gtts:
                gid = str(gtt["id"])
                if gid in self.pending and gtt["status"] == "triggered":
                    self.pending.remove(gid)
                    self.resolved[gid] = gtt.get("order_id")
        except Exception:
            pass

    def snapshot(self):
        return {
            "running": self.running,
            "pending": list(self.pending),
            "resolved": dict(self.resolved),
            "interval": self.interval,
        }
