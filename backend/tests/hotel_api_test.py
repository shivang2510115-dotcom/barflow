"""Hotel API integration tests. Requires a running server (see backend_test.py)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@barflow.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


def test_front_desk_role_exists(admin):
    r = admin.post("{}/auth/register".format(API), json={
        "email": "desk-test@barflow.io",
        "name": "Desk Tester",
        "password": "desk12345",
        "role": "front_desk",
    })
    assert r.status_code in (200, 400), r.text
    if r.status_code == 400:
        assert "exists" in r.text.lower()
