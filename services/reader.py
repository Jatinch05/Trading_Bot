# services/reader.py
# Pure Excel reading module for the trading bot

import pandas as pd

# The validator requires these columns. Extra columns are allowed.
REQUIRED_BASE_COLS = [
    "symbol", "exchange", "txn_type", "qty",
    "order_type", "price", "trigger_price",
    "product", "validity", "variety",
    "disclosed_qty", "tag",
    "gtt", "gtt_type",
    "gtt_trigger", "gtt_limit",
    "gtt_trigger_1", "gtt_limit_1",
    "gtt_trigger_2", "gtt_limit_2",
]


def read_orders_excel(file) -> pd.DataFrame:
    """
    Reads an Excel file and returns a DataFrame that is ready for validator.
    Requirements:
        - Sheet name must be "Orders"
        - Missing columns auto-created (None)
    """
    try:
        df = pd.read_excel(file, sheet_name="Orders")
    except Exception:
        raise ValueError("Excel sheet must contain 'Orders' sheet.")

    # Standardize columns to lowercase
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Replace common blanks with None
    df.replace(["", " ", "NULL", "null", "nan", "NaN"], None, inplace=True)

    # Ensure required columns exist
    for col in REQUIRED_BASE_COLS:
        if col not in df.columns:
            df[col] = None

    return df
