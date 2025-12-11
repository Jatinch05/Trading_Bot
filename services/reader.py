import pandas as pd

def read_orders_excel(file) -> pd.DataFrame:
    # expects sheet "Orders"
    try:
        return pd.read_excel(file, sheet_name="Orders")
    except Exception:
        # fallback first sheet
        return pd.read_excel(file)
