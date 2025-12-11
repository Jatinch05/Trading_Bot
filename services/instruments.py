# services/instruments.py
import csv
from pathlib import Path

class Instruments:
    def __init__(self, by_key):
        self.by_key = by_key  # {(EXCHANGE, SYMBOL): {"tick": x, "lot": y}}

    @staticmethod
    def load(path: Path = Path("data/instruments.csv")):
        if not path.exists():
            return Instruments({})
        by_key = {}
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ex = (r.get("exchange") or "").strip().upper()
                sy = (r.get("symbol") or "").strip().upper()
                tick = float(r.get("tick_size") or 0)
                lot  = int(float(r.get("lot_size") or 1))
                if ex and sy:
                    by_key[(ex, sy)] = {"tick": tick, "lot": lot}
        return Instruments(by_key)

    def get(self, exchange, symbol):
        return self.by_key.get((exchange.upper(), symbol.upper()))
