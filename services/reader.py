from io import BytesIO
import pandas as pd
from config import EXCEL_SHEET

def read_orders_excel(file_obj) -> pd.DataFrame:
    """
    Works with Streamlit's UploadedFile or a path.
    Forces openpyxl engine for .xlsx.
    """
    if hasattr(file_obj, "read"):          # Streamlit UploadedFile / file-like
        pos = file_obj.tell() if hasattr(file_obj, "tell") else 0
        file_obj.seek(0)
        data = file_obj.read()
        file_obj.seek(pos)
        return pd.read_excel(BytesIO(data), sheet_name=EXCEL_SHEET, engine="openpyxl")
    # If a filesystem path was passed
    return pd.read_excel(file_obj, sheet_name=EXCEL_SHEET, engine="openpyxl")
