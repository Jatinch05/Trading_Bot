from pathlib import Path

# Anchor .data to project folder
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / ".data"
DATA_DIR.mkdir(exist_ok=True)

# Persisted files
TOKEN_PATH = DATA_DIR / "kite_token.json"
CREDS_PATH = DATA_DIR / "kite_creds.json"

# Excel settings
EXCEL_SHEET = "Orders"
REQUIRED_COLS = [
    "symbol","exchange","txn_type","qty","order_type","price","trigger_price",
    "product","validity","variety","disclosed_qty","tag"
]

# Order placement throttling
PLACE_SLEEP_SEC = 0.20      # <- REQUIRED BY placer.py

# App UI
APP_TITLE = "Excel → Zerodha Order Bot"
