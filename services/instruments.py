import pandas as pd
from kiteconnect import KiteConnect
from config import DATA_DIR, TOKEN_PATH
from services.storage import read_json

INSTRUMENTS_CACHE = DATA_DIR / "instruments_bfo.csv"

class Instruments:
    def __init__(self, df):
        self.df = df

    @classmethod
    def load(cls, api_key=None, access_token=None):
        """
        Load instruments from cache, or return empty minimal resolver.
        We only need tradingsymbol and basic info to pass to validate().
        """
        if INSTRUMENTS_CACHE.exists():
            df = pd.read_csv(INSTRUMENTS_CACHE)
            return cls(df)

        # If no cache exists, return empty resolver (fallback)
        # NOTE: We only use direct symbol->tradingsymbol passthrough in this mode.
        df = pd.DataFrame(columns=["tradingsymbol", "exchange", "tick_size", "lot_size"])
        return cls(df)

    def resolve(self, symbol, exchange):
        """
        If instrument table loaded, use it.
        Otherwise fallback to minimal passthrough resolver.
        """
        if not self.df.empty:
            row = self.df[self.df["tradingsymbol"] == symbol]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "tradingsymbol": r["tradingsymbol"],
                    "exchange": r["exchange"],
                    "tick_size": r["tick_size"],
                    "lot_size": r["lot_size"]
                }

        # fallback resolver (no INSTRUMENTS_DIR needed)
        return {
            "tradingsymbol": symbol,
            "exchange": exchange,
            "tick_size": 0.05,
            "lot_size": 1
        }
