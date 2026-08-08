"""Stripe checkout: session creation, status polling, and webhook handling."""
import os
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import db
from routers.orders import compute_totals
from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ----------------- Stripe Checkout -----------------
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "sk_test_emergent")


class CheckoutStartIn(BaseModel):
    order_id: str
    origin_url: str  # frontend origin, e.g. https://qr-bill-hub.preview.emergentagent.com


@router.post("/payments/checkout/session")
async def create_checkout_session(payload: CheckoutStartIn, request: Request):
    """Server-driven: fetches order total from DB, creates Stripe Checkout session, persists a payment_transactions row."""
    order = await db.orders.find_one({"id": payload.order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
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
    await db.payment_transactions.insert_one(tx)
    return {"url": session.url, "session_id": session.session_id}


@router.get("/payments/checkout/status/{session_id}")
async def checkout_status(session_id: str):
    tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not tx:
        raise HTTPException(404, "Session not found")

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
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": updates})

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
    """Stripe delivers checkout.session.completed events here (idempotent)."""
    body = await request.body()
    sig = request.headers.get("Stripe-Signature") or request.headers.get("stripe-signature")
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY)
    try:
        event = await stripe_checkout.handle_webhook(body, sig) if hasattr(stripe_checkout, "handle_webhook") else None
    except Exception as e:
        logger.exception("stripe webhook parse failed")
        raise HTTPException(400, f"Webhook error: {str(e)}")

    # Fallback: parse minimal JSON if no webhook helper (test mode / emergent proxy)
    if event is None:
        import json as _json
        try:
            event = _json.loads(body.decode("utf-8"))
        except Exception:
            return {"received": True}

    session_id = None
    payment_status = None
    try:
        data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else {}
        session_id = data_obj.get("id") or event.get("session_id")
        payment_status = data_obj.get("payment_status") or event.get("payment_status")
    except Exception:
        pass

    if session_id and payment_status == "paid":
        tx = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
        if tx and tx.get("payment_status") != "paid":
            await db.payment_transactions.update_one(
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
