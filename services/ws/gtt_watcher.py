# services/ws/gtt_watcher.py

import threading
import time

class GTTWatcher:
    def __init__(self, kite):
        self.kite = kite
        self.running = False
        self.pending = set()
        self.resolved = {}
        self.interval = 2
        self._thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            self._poll()
            time.sleep(self.interval)

    def _poll(self):
        try:
            for gtt in self.kite.get_gtts():
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
