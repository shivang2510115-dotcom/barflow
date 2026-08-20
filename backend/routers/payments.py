"""Stripe checkout: session creation, status polling, and webhook handling.

None of these three routes has a caller to scope from — the first two are reached by a
guest paying from the QR page, and the third by Stripe itself — so the hotel is resolved
from the **order** being paid for, exactly as the QR order routes resolve theirs from the
table. `payment_transactions` stands outside tenancy: it is Stripe's session ledger,
keyed by a session id, and it is what the property is looked up *through* rather than a
record a hotel reads. Every order and table write below goes through the scoped handle
that lookup produced.
"""
import hashlib
import hmac
import json
import os
import time
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import unscoped_db
from routers.orders import compute_totals
from scoped_db import db_for_order
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------- Stripe Checkout -----------------
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "sk_test_emergent")

# How old a signed event may be and still be accepted. Stripe's own libraries default to
# five minutes, and the number is what stops a captured request being replayed later:
# the timestamp is inside the signature, so an attacker can neither age it nor freshen it.
WEBHOOK_TOLERANCE_SECONDS = 300


def webhook_secret() -> str:
    """The signing secret, read per request rather than at import.

    Per request so that a deployment which sets the variable and restarts is configured,
    and so that this is testable without re-importing the module. Blank and unset are
    the same thing: a dashboard field somebody meant to fill in.
    """
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()


def verify_stripe_event(body: bytes, header: str | None, secret: str,
                        now: float | None = None) -> dict:
    """The signed event this body carries, or a 400.

    Stripe signs `timestamp.body` with HMAC-SHA256 and sends
    `t=<timestamp>,v1=<hex>[,v1=<hex>]` — more than one v1 while a secret is being
    rotated. This is deliberately implemented here rather than delegated to the SDK: the
    route's whole authentication is this check, and the code it used to call was optional
    at runtime, so "the helper is missing" silently became "believe the body".

    Everything that fails is the same 400 with the same wording. Telling a caller which
    part of their forgery was wrong is a free oracle.
    """
    bad = HTTPException(400, "Invalid Stripe signature")
    if not header:
        raise bad

    timestamp = None
    signatures = []
    for part in header.split(","):
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise bad

    try:
        issued_at = int(timestamp)
    except ValueError:
        raise bad
    # Both directions. A timestamp far in the future is as much a replay handle as one
    # far in the past — it would stay valid for as long as the clock took to catch up.
    if abs((time.time() if now is None else now) - issued_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise bad

    expected = hmac.new(secret.encode("utf-8"), f"{issued_at}.".encode("utf-8") + body,
                        hashlib.sha256).hexdigest()
    # compare_digest, not ==: the comparison is against an attacker-supplied string.
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise bad

    try:
        event = json.loads(body.decode("utf-8"))
    except Exception:
        raise bad
    if not isinstance(event, dict):
        raise bad
    return event


class CheckoutStartIn(BaseModel):
    order_id: str
    origin_url: str  # frontend origin, e.g. https://qr-bill-hub.preview.emergentagent.com


@router.post("/payments/checkout/session")
async def create_checkout_session(payload: CheckoutStartIn, request: Request):
    """Server-driven: fetches order total from DB, creates Stripe Checkout session, persists a payment_transactions row."""
    # 404 when the hotel is not live, the same answer an unknown order gets: a suspended
    # property must not be able to take a rupee through this door.
    db, order = await db_for_order(payload.order_id)
    if order["status"] != "open":
        raise HTTPException(400, "Order is not open")

    # Recompute total server-side (do not trust client)
    order = compute_totals(dict(order))
    total = order["total"]
    if total <= 0:
        raise HTTPException(400, "Order total is zero")

    origin = payload.origin_url.rstrip("/")
    host_url = f"{origin}/api"
    webhook_url = f"{host_url}/webhook/stripe"
    success_url = f"{origin}/t/{order['table_id']}?paid=1&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/t/{order['table_id']}?paid=0"

    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    req = CheckoutSessionRequest(
        amount=float(total),
        currency="usd",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "order_id": order["id"],
            "table_id": order["table_id"],
            "table_label": order["table_label"],
            "source": "qr",
        },
    )
    try:
        session = await stripe_checkout.create_checkout_session(req)
    except Exception as e:
        logger.exception("stripe session create failed")
        raise HTTPException(502, f"Stripe error: {str(e)}")

    tx = {
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "order_id": order["id"],
        "table_id": order["table_id"],
        "amount": total,
        "currency": "usd",
        "payment_status": "initiated",
        "status": "open",
        "metadata": req.metadata,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await unscoped_db.payment_transactions.insert_one(tx)
    return {"url": session.url, "session_id": session.session_id}


@router.get("/payments/checkout/status/{session_id}")
async def checkout_status(session_id: str):
    tx = await unscoped_db.payment_transactions.find_one(
        {"session_id": session_id}, {"_id": 0})
    if not tx:
        raise HTTPException(404, "Session not found")
    # The order the session was opened for names the hotel; everything written below is
    # written through its handle.
    db, _order = await db_for_order(tx["order_id"])

    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY)
    try:
        status_resp = await stripe_checkout.get_checkout_status(session_id)
    except Exception as e:
        raise HTTPException(502, f"Stripe error: {str(e)}")

    updates = {
        "payment_status": status_resp.payment_status,
        "session_status": status_resp.status,
    }
    # settle order idempotently on paid
    if status_resp.payment_status == "paid" and tx.get("payment_status") != "paid":
        order = await db.orders.find_one({"id": tx["order_id"]}, {"_id": 0})
        if order and order["status"] == "open":
            order = compute_totals(dict(order))
            order["status"] = "settled"
            order["payment_method"] = "online"
            order["settled_at"] = datetime.now(timezone.utc).isoformat()
            order.pop("_id", None)
            await db.orders.update_one({"id": order["id"]}, {"$set": order})
            await db.tables.update_one(
                {"id": order["table_id"]},
                {"$set": {"status": "free", "current_order_id": None}},
            )
        updates["settled_at"] = datetime.now(timezone.utc).isoformat()
    await unscoped_db.payment_transactions.update_one(
        {"session_id": session_id}, {"$set": updates})

    return {
        "payment_status": status_resp.payment_status,
        "status": status_resp.status,
        "amount_total": status_resp.amount_total,
        "currency": status_resp.currency,
        "order_id": tx["order_id"],
        "table_id": tx["table_id"],
    }


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Stripe delivers checkout.session.completed events here (idempotent).

    This route has no caller to authenticate — Stripe holds no login — so the signature
    *is* the authentication, and settling a bill is what believing it does. There is no
    fallback: a body that is not verified is not read.
    """
    secret = webhook_secret()
    if not secret:
        # Switched off rather than trusting. An unverifiable webhook that accepts
        # anything lets a stranger settle any order on the platform; one that refuses
        # everything only means online payments are not settled by webhook until the
        # variable is set. 503 rather than 400 on purpose: Stripe retries a 5xx for
        # about three days and gives up on a 4xx, so configuring the secret recovers
        # the events that arrived during the misconfiguration instead of losing them.
        logger.error(
            "STRIPE_WEBHOOK_SECRET is not set — refusing every Stripe webhook. "
            "Online payments will not settle until it is configured.")
        raise HTTPException(503, "Stripe webhooks are not configured on this deployment")

    body = await request.body()
    event = verify_stripe_event(body, request.headers.get("stripe-signature"), secret)

    session_id = None
    payment_status = None
    try:
        data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else {}
        session_id = data_obj.get("id") or event.get("session_id")
        payment_status = data_obj.get("payment_status") or event.get("payment_status")
    except Exception:
        pass

    if session_id and payment_status == "paid":
        tx = await unscoped_db.payment_transactions.find_one(
            {"session_id": session_id}, {"_id": 0})
        if tx and tx.get("payment_status") != "paid":
            db, _order = await db_for_order(tx["order_id"])
            await unscoped_db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {"payment_status": "paid", "settled_at": datetime.now(timezone.utc).isoformat()}},
            )
            order = await db.orders.find_one({"id": tx["order_id"]}, {"_id": 0})
            if order and order["status"] == "open":
                order = compute_totals(dict(order))
                order["status"] = "settled"
                order["payment_method"] = "online"
                order["settled_at"] = datetime.now(timezone.utc).isoformat()
                order.pop("_id", None)
                await db.orders.update_one({"id": order["id"]}, {"$set": order})
                await db.tables.update_one(
                    {"id": order["table_id"]},
                    {"$set": {"status": "free", "current_order_id": None}},
                )
    return {"received": True}
