from kiteconnect import KiteConnect
from services.storage import write_json, read_json

class KiteAuth:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        
        # add a sane timeout so calls don’t hang forever
        self.kite = KiteConnect(api_key=api_key, timeout=8)   # <-- add timeout

    def login_url(self):
        return self.kite.login_url()

    def exchange_request_token(self, request_token):
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        self.kite.set_access_token(access_token)
        return access_token

    def load_saved_token(self):
        data = read_json(self.token_path, {})
        return data.get("access_token")
