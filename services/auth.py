from kiteconnect import KiteConnect

class KiteAuth:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.kite = KiteConnect(api_key=self.api_key)  # no disk persistence

    def login_url(self) -> str:
        return self.kite.login_url()

    def exchange_request_token(self, request_token: str) -> str:
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        token = data["access_token"]
        self.kite.set_access_token(token)
        return token
