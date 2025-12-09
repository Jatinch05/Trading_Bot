import io
import pandas as pd
from datetime import datetime

def dataframe_to_excel_download(df: pd.DataFrame) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Results", index=False)
    fname = f"orders_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return buf.getvalue(), fname
