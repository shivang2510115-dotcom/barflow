"""The Stripe webhook: who is allowed to tell this application that money arrived.

`POST /api/webhook/stripe` is anonymous by necessity — Stripe holds no login — so the
signature *is* the authentication. Until this file existed the route had a fallback that
parsed the raw body as JSON and believed it whenever the SDK offered no `handle_webhook`,
which made "an order was paid for" something any stranger could assert with one curl.

No server here: the route is an ordinary coroutine and is called as one, the same style
as test_isolation.py. What is exercised is the real settlement path — the real order, the
real table, the real scoped handle — with only the transport absent.
"""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

import db as db_module
import routers.payments as payments
from mock_db import MockDatabase

SECRET = "whsec_test_secret_value"


def run(coro):
    return asyncio.run(coro)


class FakeRequest:
    """Everything the route reads off a request, and nothing else."""

    def __init__(self, body: bytes, signature: str | None = None):
        self._body = body
        headers = {"stripe-signature": signature} if signature is not None else {}
        self.headers = Headers(headers)

    async def body(self) -> bytes:
        return self._body


def signed(body: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    """The header Stripe sends: a timestamp and an HMAC over `timestamp.body`."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                      hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def event_body(session_id: str = "sess_live") -> bytes:
    return json.dumps({
        "id": "evt_1", "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "payment_status": "paid"}},
    }).encode()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """One live hotel with an occupied table, an open order, and an unpaid session."""
    handle = MockDatabase(str(tmp_path / "db.json"))
    monkeypatch.setattr(db_module, "unscoped_db", handle)
    monkeypatch.setattr(payments, "unscoped_db", handle)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)

    now = datetime.now(timezone.utc).isoformat()
    run(handle.properties.insert_one(
        {"id": "p1", "name": "The Grand", "status": "live", "created_at": now}))
    run(handle.tables.insert_one(
        {"id": "t1", "label": "T01", "capacity": 4, "zone": "Bar", "status": "occupied",
         "current_order_id": "o1", "property_id": "p1"}))
    run(handle.orders.insert_one(
        {"id": "o1", "table_id": "t1", "table_label": "T01", "property_id": "p1",
         "items": [{"id": "i1", "menu_item_id": "m1", "name": "Negroni", "price": 500.0,
                    "quantity": 1, "station": "bar", "notes": "", "status": "served",
                    "created_at": now}],
         "status": "open", "subtotal": 500.0, "tax": 50.0, "discount": 0.0,
         "total": 550.0, "payment_method": None, "source": "qr", "created_at": now}))
    run(handle.payment_transactions.insert_one(
        {"id": "tx1", "session_id": "sess_live", "order_id": "o1", "table_id": "t1",
         "amount": 550.0, "currency": "inr", "payment_status": "initiated",
         "status": "open", "metadata": {}, "created_at": now}))
    return handle


def order(handle) -> dict:
    return run(handle.orders.find_one({"id": "o1"}, {"_id": 0}))


def table(handle) -> dict:
    return run(handle.tables.find_one({"id": "t1"}, {"_id": 0}))


def refused(handle, request) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        run(payments.stripe_webhook(request))
    # Whatever the refusal was, nothing moved.
    assert order(handle)["status"] == "open"
    assert table(handle)["status"] == "occupied"
    assert run(handle.payment_transactions.find_one(
        {"session_id": "sess_live"}))["payment_status"] == "initiated"
    return exc.value


# ------------------------------- the hole itself -------------------------------
def test_an_unsigned_crafted_body_does_not_settle_the_order(world):
    """The exploit this file was written for.

    An anonymous POST of a body naming a real session id used to settle the bill and
    free the table without a rupee moving. It is refused before it is read.
    """
    assert refused(world, FakeRequest(event_body())).status_code == 400


def test_the_sdk_fallback_is_gone(world, monkeypatch):
    """The precise condition the old code fell back under: an SDK with no webhook helper.

    Verification is this application's own now, so an SDK that cannot verify a signature
    no longer means the body is believed — it means nothing at all, because the SDK is
    not consulted on this path.
    """
    class NoHelper:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(payments, "StripeCheckout", NoHelper)
    assert refused(world, FakeRequest(event_body())).status_code == 400


# ------------------------------- verification -------------------------------
def test_a_signature_from_the_wrong_secret_is_refused(world):
    body = event_body()
    assert refused(world, FakeRequest(body, signed(body, "whsec_someone_elses"))
                   ).status_code == 400


def test_a_signature_over_a_different_body_is_refused(world):
    """The replay-with-substitution attempt: a real header, a swapped payload."""
    header = signed(event_body("sess_other"))
    assert refused(world, FakeRequest(event_body("sess_live"), header)).status_code == 400


def test_a_stale_timestamp_is_refused(world):
    """A captured event, correctly signed, replayed a day later."""
    body = event_body()
    stale = signed(body, timestamp=int(time.time()) - 86_400)
    assert refused(world, FakeRequest(body, stale)).status_code == 400


def test_a_malformed_signature_header_is_refused(world):
    body = event_body()
    for header in ("", "garbage", "t=,v1=", f"v1={'0' * 64}", "t=notanumber,v1=abc"):
        assert refused(world, FakeRequest(body, header)).status_code == 400


def test_a_correctly_signed_event_settles_the_order_and_frees_the_table(world):
    body = event_body()
    assert run(payments.stripe_webhook(FakeRequest(body, signed(body)))) == {"received": True}

    settled = order(world)
    assert settled["status"] == "settled"
    assert settled["payment_method"] == "online"
    assert table(world)["status"] == "free"
    assert table(world)["current_order_id"] is None


def test_a_correctly_signed_event_is_idempotent(world):
    """Stripe retries. The second delivery must not settle anything a second time."""
    body = event_body()
    run(payments.stripe_webhook(FakeRequest(body, signed(body))))
    first = order(world)["settled_at"]
    run(payments.stripe_webhook(FakeRequest(body, signed(body))))
    assert order(world)["settled_at"] == first


# --------------------------- an unconfigured deployment ---------------------------
def test_with_no_secret_configured_every_request_is_refused(world, monkeypatch):
    """Including one that is correctly signed — there is nothing to verify it against.

    A webhook that cannot check a signature and accepts anything is worse than one that
    is switched off, so an unset STRIPE_WEBHOOK_SECRET switches it off.
    """
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    body = event_body()
    assert refused(world, FakeRequest(body, signed(body))).status_code == 503
    assert refused(world, FakeRequest(body)).status_code == 503


def test_a_blank_secret_counts_as_unconfigured(world, monkeypatch):
    """`STRIPE_WEBHOOK_SECRET=""` in a dashboard is somebody who meant to fill it in."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "   ")
    assert refused(world, FakeRequest(event_body())).status_code == 503
