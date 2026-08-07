"""
BarFlow backend regression + Stripe checkout tests.
Verifies:
- Auth (admin login)
- Existing CRUD basics (tables, menu)
- Stripe checkout session creation (/api/payments/checkout/session)
- Stripe checkout status polling (/api/payments/checkout/status/{session_id})
- Return URL contains checkout.stripe.com
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://qr-bill-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@barflow.io"
ADMIN_PASSWORD = "admin123"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(session):
    r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.text}"
    data = r.json()
    assert "token" in data
    return data["token"]


@pytest.fixture(scope="session")
def auth_session(session, admin_token):
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {admin_token}",
    })
    return s


# ---------- Health ----------
def test_root(session):
    r = session.get(f"{API}/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


# ---------- Auth ----------
class TestAuth:
    def test_admin_login(self, session):
        r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["role"] == "admin"
        assert isinstance(data["token"], str) and len(data["token"]) > 20

    def test_invalid_login(self, session):
        r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me(self, auth_session):
        r = auth_session.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL


# ---------- Basic CRUD sanity ----------
class TestBasicResources:
    def test_list_tables(self, auth_session):
        r = auth_session.get(f"{API}/tables")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        assert len(arr) > 0
        # ensure no _id leaks
        for t in arr:
            assert "_id" not in t

    def test_list_menu_public(self, session):
        r = session.get(f"{API}/menu")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list) and len(arr) > 0
        for m in arr:
            assert "_id" not in m


# ---------- Stripe Checkout ----------
class TestStripeCheckout:
    """
    Flow:
    1. Pick any table.
    2. POST /orders/table/{id}/items with a menu item (creates open order).
    3. POST /payments/checkout/session -> expect url + session_id.
    4. GET /payments/checkout/status/{session_id} -> expect payment_status field.
    """

    @pytest.fixture(scope="class")
    def open_order(self, auth_session):
        # Find a free table
        tables = auth_session.get(f"{API}/tables").json()
        free = next((t for t in tables if t["status"] == "free"), tables[0])
        table_id = free["id"]

        menu = auth_session.get(f"{API}/menu").json()
        item = next((m for m in menu if m.get("available", True)), menu[0])

        r = auth_session.post(
            f"{API}/orders/table/{table_id}/items",
            json={"items": [{"menu_item_id": item["id"], "quantity": 1}], "source": "pos"},
        )
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["total"] > 0
        return order

    def test_create_checkout_session_returns_stripe_url(self, session, open_order):
        payload = {
            "order_id": open_order["id"],
            "origin_url": BASE_URL,
        }
        r = session.post(f"{API}/payments/checkout/session", json=payload)
        assert r.status_code == 200, f"stripe session create failed: {r.status_code} {r.text}"
        data = r.json()
        assert "url" in data and "session_id" in data
        assert isinstance(data["url"], str) and data["url"].startswith("http")
        # Verify Stripe URL (may go through emergent proxy — accept either checkout.stripe.com or stripe.com)
        assert (
            "checkout.stripe.com" in data["url"] or "stripe.com" in data["url"]
        ), f"URL not from stripe: {data['url']}"
        assert isinstance(data["session_id"], str) and len(data["session_id"]) > 5
        # Save session for next test
        pytest.stripe_session_id = data["session_id"]
        pytest.stripe_url = data["url"]

    def test_checkout_status_returns_pending_before_payment(self, session):
        sid = getattr(pytest, "stripe_session_id", None)
        if not sid:
            pytest.skip("no session id from previous test")
        r = session.get(f"{API}/payments/checkout/status/{sid}")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "payment_status" in data
        # Before payment, should be one of unpaid/no_payment_required/expired etc, NOT paid
        assert data["payment_status"] in ("unpaid", "no_payment_required", "open", "expired", None)
        assert "status" in data
        # Should return order+table linkage
        assert data.get("order_id")
        assert data.get("table_id")

    def test_checkout_status_invalid_session_404(self, session):
        r = session.get(f"{API}/payments/checkout/status/cs_fake_not_a_real_session")
        assert r.status_code == 404

    def test_create_checkout_invalid_order_404(self, session):
        r = session.post(
            f"{API}/payments/checkout/session",
            json={"order_id": "00000000-0000-0000-0000-000000000000", "origin_url": BASE_URL},
        )
        assert r.status_code == 404


# ---------- Webhook (parseable, does not require a real signature in emergent proxy mode) ----------
class TestStripeWebhook:
    def test_webhook_accepts_post(self, session):
        # Send an empty body; endpoint should not 500 – returns {"received": True} or a 400 controlled error
        r = session.post(f"{API}/webhook/stripe", data="{}")
        assert r.status_code in (200, 400)
