from pydantic import BaseModel
import uuid

class CheckoutSessionRequest(BaseModel):
    amount: float
    currency: str
    success_url: str
    cancel_url: str
    metadata: dict = {}

class MockSessionResponse:
    def __init__(self, session_id, url):
        self.session_id = session_id
        self.url = url

class MockStatusResponse:
    def __init__(self, session_id, amount):
        self.session_id = session_id
        self.payment_status = "paid"
        self.status = "complete"
        self.amount_total = amount
        self.currency = "usd"

class StripeCheckout:
    def __init__(self, api_key, webhook_url=None):
        self.api_key = api_key
        self.webhook_url = webhook_url

    async def create_checkout_session(self, req: CheckoutSessionRequest):
        sess_id = f"sess_{uuid.uuid4().hex}"
        url = req.success_url.replace("{CHECKOUT_SESSION_ID}", sess_id)
        return MockSessionResponse(sess_id, url)

    async def get_checkout_status(self, session_id: str):
        return MockStatusResponse(session_id, 10.0)

    async def handle_webhook(self, body, sig):
        return {"type": "checkout.session.completed"}
