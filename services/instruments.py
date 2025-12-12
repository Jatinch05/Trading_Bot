# services/instruments.py — robust against non-string dtypes in instruments file

import json
import pandas as pd
from pathlib import Path

INSTRUMENTS_PATH = Path("data/instruments.csv")  # or .json

class Instruments:
    def __init__(self, df: pd.DataFrame):
        # Ensure required columns exist
        for c in ("exchange", "tradingsymbol"):
            if c not in df.columns:
                df[c] = ""
        # Coerce to string and normalize once
        df["exchange"] = df["exchange"].astype(str).fillna("").str.strip()
        df["tradingsymbol"] = df["tradingsymbol"].astype(str).fillna("").str.strip()
        self.df = df

    @staticmethod
    def load():
        if not INSTRUMENTS_PATH.exists():
            return Instruments(pd.DataFrame({"exchange": [], "tradingsymbol": []}))
        if INSTRUMENTS_PATH.suffix.lower() == ".csv":
            df = pd.read_csv(INSTRUMENTS_PATH)
        else:
            df = pd.read_json(INSTRUMENTS_PATH)

        # Normalize column names once
        df.columns = [str(c).lower() for c in df.columns]
        # Keep only what we need to avoid dtype surprises
        keep = {}
        keep["exchange"] = df["exchange"] if "exchange" in df.columns else ""
        keep["tradingsymbol"] = df["tradingsymbol"] if "tradingsymbol" in df.columns else ""
        df_norm = pd.DataFrame(keep)

        return Instruments(df_norm)

    def exists(self, exchange: str, symbol: str) -> bool:
        ex = str(exchange).strip().upper()
        sy = str(symbol).strip().upper()
        # Coerce to uppercase safely even if original dtypes were numeric
        ex_series = self.df["exchange"].astype(str).str.upper()
        sy_series = self.df["tradingsymbol"].astype(str).str.upper()
        return not self.df[(ex_series == ex) & (sy_series == sy)].empty

    def validate(self, exchange: str, symbol: str):
        if not self.exists(exchange, symbol):
            raise ValueError(f"Instrument {exchange}:{symbol} not found")
