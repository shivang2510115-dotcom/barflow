"""Sales reports, analytics, and the daily owner brief (WhatsApp)."""
import os
import asyncio
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import unscoped_db
from scoped_db import PropertyScopedDatabase, tenant_db
from security import require_access
# Outlet sales analytics cover both the restaurant and the bar, so either domain grants
# access. The hotel revenue report is a later sub-project and will declare "hotel".
from services.access import LIVE, OUTLET
# `settled_at` is stored in UTC; every report here is a report on the property's local
# day. The same conversion the analytics endpoint uses, so the two screens can never
# disagree about which day a bill belongs to. See services/clock.py.
from services.clock import local_date, now_local, today as local_today

logger = logging.getLogger(__name__)

router = APIRouter()

# Everything under /reports is the outlet Reports screen.
REPORTS = require_access(OUTLET, "admin", "manager", permission="outlet.reports")


# ----------------- Reports -----------------
@router.get("/reports/summary")
async def report_summary(user: dict = Depends(REPORTS),
                         db: PropertyScopedDatabase = Depends(tenant_db)):
    today = local_today()
    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(2000)
    today_orders = [o for o in settled if local_date(o.get("settled_at")) == today]
    revenue_today = sum(o["total"] for o in today_orders)
    revenue_total = sum(o["total"] for o in settled)

    # top items
    counts: dict = {}
    for o in settled:
        for it in o["items"]:
            counts[it["name"]] = counts.get(it["name"], 0) + it["quantity"]
    top_items = sorted(counts.items(), key=lambda x: -x[1])[:5]

    # last 7 days revenue
    days = {}
    for o in settled:
        d = local_date(o.get("settled_at"))
        if d:
            days[d] = days.get(d, 0) + o["total"]
    daily = sorted(days.items())[-7:]

    tables_count = await db.tables.count_documents({})
    occupied = await db.tables.count_documents({"status": "occupied"})
    low_stock = await db.inventory.count_documents({"$expr": {"$lte": ["$stock", "$threshold"]}})

    return {
        "revenue_today": round(revenue_today, 2),
        "revenue_total": round(revenue_total, 2),
        "orders_today": len(today_orders),
        "orders_total": len(settled),
        "tables_total": tables_count,
        "tables_occupied": occupied,
        "low_stock_count": low_stock,
        "top_items": [{"name": n, "qty": q} for n, q in top_items],
        "daily_revenue": [{"date": d, "revenue": round(r, 2)} for d, r in daily],
    }


@router.get("/reports/orders")
async def recent_orders(user: dict = Depends(REPORTS),
                        db: PropertyScopedDatabase = Depends(tenant_db)):
    return await db.orders.find({"status": "settled"}, {"_id": 0}).sort("settled_at", -1).limit(50).to_list(50)


@router.get("/reports/analytics")
async def report_analytics(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: str = "day",
    user: dict = Depends(REPORTS),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    """Sales analytics for a date range (inclusive, by settled_at date).

    - granularity "day" -> YYYY-MM-DD buckets; "month" -> YYYY-MM buckets.
    - Defaults to the current month when start/end are omitted.
    - Returns range KPIs, a time series, top items, payment mix and top customers.
    """
    if granularity not in ("day", "month"):
        granularity = "day"

    today = date.fromisoformat(local_today())
    if not end:
        end = today.isoformat()
    if not start:
        start = today.replace(day=1).isoformat()

    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(100000)
    in_range = []
    for o in settled:
        d = local_date(o.get("settled_at"))
        if d and start <= d <= end:
            in_range.append(o)

    revenue = sum((o.get("total") or 0) for o in in_range)
    orders_count = len(in_range)
    items_sold = sum(it.get("quantity", 0) for o in in_range for it in o.get("items", []))
    avg_order = (revenue / orders_count) if orders_count else 0

    # Time series (bucketed by day or month)
    buckets: dict = {}
    for o in in_range:
        d = local_date(o.get("settled_at")) or ""
        key = d[:7] if granularity == "month" else d
        b = buckets.setdefault(key, {"revenue": 0.0, "orders": 0})
        b["revenue"] += (o.get("total") or 0)
        b["orders"] += 1
    series = [
        {"bucket": k, "revenue": round(v["revenue"], 2), "orders": v["orders"]}
        for k, v in sorted(buckets.items())
    ]

    # Top selling items (by quantity), with revenue
    item_stats: dict = {}
    for o in in_range:
        for it in o.get("items", []):
            name = it.get("name", "Unknown")
            e = item_stats.setdefault(name, {"qty": 0, "revenue": 0.0})
            e["qty"] += it.get("quantity", 0) or 0
            e["revenue"] += (it.get("price", 0) or 0) * (it.get("quantity", 0) or 0)
    top_items = sorted(
        ({"name": n, "qty": v["qty"], "revenue": round(v["revenue"], 2)} for n, v in item_stats.items()),
        key=lambda x: -x["qty"],
    )[:10]

    # Payment method breakdown
    pay: dict = {}
    for o in in_range:
        m = o.get("payment_method") or "unknown"
        e = pay.setdefault(m, {"revenue": 0.0, "orders": 0})
        e["revenue"] += (o.get("total") or 0)
        e["orders"] += 1
    payment_mix = [
        {"method": m, "revenue": round(v["revenue"], 2), "orders": v["orders"]}
        for m, v in sorted(pay.items(), key=lambda x: -x[1]["revenue"])
    ]

    # Top customers by revenue (keyed by phone when present, else name)
    cust: dict = {}
    for o in in_range:
        name = (o.get("customer_name") or "").strip()
        if not name:
            continue
        phone = (o.get("customer_phone") or "").strip()
        key = phone or name.lower()
        e = cust.setdefault(key, {"name": name, "phone": phone, "revenue": 0.0, "orders": 0})
        e["revenue"] += (o.get("total") or 0)
        e["orders"] += 1
    top_customers = sorted(
        (
            {"name": v["name"], "phone": v["phone"], "revenue": round(v["revenue"], 2), "orders": v["orders"]}
            for v in cust.values()
        ),
        key=lambda x: -x["revenue"],
    )[:10]

    return {
        "range": {"start": start, "end": end, "granularity": granularity},
        "totals": {
            "revenue": round(revenue, 2),
            "orders": orders_count,
            "avg_order_value": round(avg_order, 2),
            "items_sold": items_sold,
        },
        "series": series,
        "top_items": top_items,
        "payment_mix": payment_mix,
        "top_customers": top_customers,
    }


# ----------------- Daily Owner Brief (WhatsApp) -----------------
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "₹")


def _indian_grouping(n: int) -> str:
    """Group digits the Indian way: 12,34,567 rather than 1,234,567.

    The screens format money through currency() in the frontend, which uses en-IN.
    Python's "," format spec is Western-only, so without this the same figure reads
    Rs1,00,000 in the app and Rs100,000 in the owner's WhatsApp message, and the two
    look like different numbers to the person reconciling them.
    """
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        # Every group above the last three is a pair, hence the step of 2.
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return s


def _money(v):
    n = round(v or 0)
    # Sign outside the symbol: -Rs200, not Rs-200. The latter reads as a typo.
    return f"{'-' if n < 0 else ''}{CURRENCY_SYMBOL}{_indian_grouping(abs(n))}"


async def build_daily_brief(db, date: Optional[str] = None) -> dict:
    """Compute one property's end-of-day summary for a single date (settled_at)."""
    if not date:
        date = local_today()

    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(100000)
    day = [o for o in settled if local_date(o.get("settled_at")) == date]

    revenue = sum((o.get("total") or 0) for o in day)
    bills = len(day)
    items_sold = sum(it.get("quantity", 0) for o in day for it in o.get("items", []))

    # top items today
    counts: dict = {}
    for o in day:
        for it in o.get("items", []):
            counts[it["name"]] = counts.get(it["name"], 0) + (it.get("quantity", 0) or 0)
    top_items = sorted(counts.items(), key=lambda x: -x[1])[:3]

    # best customer today (by spend)
    cust: dict = {}
    for o in day:
        name = (o.get("customer_name") or "").strip()
        if not name:
            continue
        key = (o.get("customer_phone") or "").strip() or name.lower()
        e = cust.setdefault(key, {"name": name, "revenue": 0.0})
        e["revenue"] += (o.get("total") or 0)
    best = max(cust.values(), key=lambda x: x["revenue"], default=None)

    # payment split
    pay: dict = {}
    for o in day:
        m = o.get("payment_method") or "other"
        pay[m] = pay.get(m, 0) + (o.get("total") or 0)

    low = await db.inventory.find({"$expr": {"$lte": ["$stock", "$threshold"]}}, {"_id": 0}).to_list(1000)
    low_names = [i["name"] for i in low]

    # WhatsApp-friendly text
    lines = [f"🍸 *BarFlow — Daily Brief*  ({date})", ""]
    lines.append(f"💰 Revenue: *{_money(revenue)}*")
    lines.append(f"🧾 Bills: {bills}  ·  Items sold: {items_sold}")
    if pay:
        lines.append("💳 " + " · ".join(f"{k}: {_money(v)}" for k, v in sorted(pay.items(), key=lambda x: -x[1])))
    if top_items:
        lines.append("🔥 Top: " + ", ".join(f"{n} ×{q}" for n, q in top_items))
    if best:
        lines.append(f"⭐ Best customer: {best['name']} ({_money(best['revenue'])})")
    lines.append("📦 Low stock: " + (", ".join(low_names) if low_names else "all good ✅"))
    lines.append("")
    lines.append("— sent automatically by BarFlow")
    message = "\n".join(lines)

    return {
        "date": date,
        "revenue": round(revenue, 2),
        "bills": bills,
        "items_sold": items_sold,
        "top_items": [{"name": n, "qty": q} for n, q in top_items],
        "best_customer": best,
        "payment_split": pay,
        "low_stock": low_names,
        "message": message,
    }


def whatsapp_config_problem(need_owner_phone: bool = True) -> str:
    """What is missing, in the words of the thing that has to be fixed, or "".

    Named individually rather than as one "not configured": each of these is a different
    page of the provider's dashboard, and being told which one is the difference between
    five minutes and an afternoon.

    `need_owner_phone` is False when the recipient is not the owner. OWNER_PHONE is where
    the nightly brief goes; a birthday greeting goes to the customer, and citing a missing
    OWNER_PHONE as the reason a customer could not be messaged sends whoever reads it to
    fix the wrong thing.
    """
    missing = []
    if not os.environ.get("WHATSAPP_TOKEN"):
        missing.append("WHATSAPP_TOKEN (the provider's API token)")
    if not os.environ.get("WHATSAPP_PHONE_ID"):
        missing.append("WHATSAPP_PHONE_ID (the Phone number ID, not the phone number)")
    if need_owner_phone and not (os.environ.get("OWNER_PHONE") or "").strip():
        missing.append("OWNER_PHONE (recipient, with country code, digits only)")
    return "Not configured: " + "; ".join(missing) if missing else ""


# Meta returns these for the mistakes that actually happen, and the raw text of each is
# not something a hotelier can act on. Translated where we recognise the code, passed
# through verbatim where we do not — a message we cannot explain is still better than
# one we swallow.
_WHATSAPP_ERRORS = {
    131030: "That number is not in the recipient allow-list. A WhatsApp app in "
            "development mode can only message numbers you have added under "
            "API Setup -> To.",
    131047: "More than 24 hours since that number last messaged you, so a plain text "
            "message is refused. Send an approved template instead, or have them "
            "message the business number first.",
    131026: "That number cannot receive WhatsApp messages — check the country code, "
            "and that it is a WhatsApp account.",
    132000: "The template exists but its parameter count does not match what was sent.",
    132001: "No approved template with that name and language.",
    190: "The access token has expired or been revoked. Generate a new one under "
         "API Setup; the temporary token there lasts 24 hours.",
    100: "The request was rejected as malformed — most often WHATSAPP_PHONE_ID holding "
         "the phone number rather than the Phone number ID.",
}


def _send_whatsapp(to: str, text: str) -> dict:
    """Send via Meta's WhatsApp Cloud API, and report exactly what happened.

    Never claims success it did not have. With no credentials this used to log at info
    level and return a shape that read like a send, so a misconfigured deployment and a
    working one were indistinguishable — the failure this function exists to make
    visible.

    Free-form text, which Meta only accepts within 24 hours of the customer messaging the
    business. That is fine for the two things that use it — the owner's own nightly brief
    and the test message an admin sends to their own phone, both to a number that has
    opted into the conversation. Anything sent to a *customer* days after a visit is
    outside that window and has to be an approved template: see `send_whatsapp_template`
    below, which is the same transport with a different body.
    """
    problem = whatsapp_config_problem()
    if problem:
        logger.warning("WhatsApp not sent — %s", problem)
        return {"sent": False, "configured": False, "to": to, "error": problem,
                "message": text}
    return _post_whatsapp(to, {"type": "text", "text": {"body": text}})


def send_whatsapp_template(to: str, template: str, language: str,
                           variables: list) -> dict:
    """Send an approved template, with its variables filled in, and say what happened.

    The only way to reach a customer outside the 24-hour window, and therefore the only
    way a birthday greeting or a post-visit note can go at all. `template` is a name Meta
    has reviewed and approved under this business account; `variables` are the positional
    body parameters that fill its `{{1}}`, `{{2}}` … in order. Nothing here writes a
    sentence — the words are Meta's, held against the name.

    Public, unlike `_send_whatsapp`, because `routers/messaging.py` is the caller and the
    import needs to read as the deliberate crossing that it is.

    Two config checks, not one, and they refuse in different words on purpose.
    `whatsapp_config_problem()` covers the credentials — the same three environment
    variables, named individually, that the status endpoint already reports. Whether a
    *template* exists is the property's own configuration and is checked before this is
    ever called (services/messaging.py::template_problem), because it is fixed in a
    different place by a different person. `OWNER_PHONE` being required by
    `whatsapp_config_problem()` is a quirk this inherits: it is the brief's recipient and
    has nothing to do with a customer's number, but a deployment missing it is one nobody
    has finished setting up, so refusing is the honest answer either way.
    """
    problem = whatsapp_config_problem()
    if problem:
        logger.warning("WhatsApp template %r not sent — %s", template, problem)
        return {"sent": False, "configured": False, "to": to, "error": problem,
                "template": template}

    body = {
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            # Body parameters only. A header or a button variable would be another
            # component here, and neither is asked for by anything this application
            # sends — an unused empty `components` entry is a shape Meta rejects.
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in variables],
            }] if variables else [],
        },
    }
    result = _post_whatsapp(to, body)
    result["template"] = template
    return result


def _post_whatsapp(to: str, message: dict) -> dict:
    """The one HTTP call, and the one reading of what came back.

    Split out of `_send_whatsapp` when templates arrived rather than copied, because the
    valuable half of that function was never the request — it was the refusal handling
    below, which turns Meta's error codes into something a hotelier can act on. A second
    copy of that would drift, and its first divergence would be a failed birthday message
    reported as an unexplained 400.

    The caller has already established that the credentials are present; `message` is
    whatever body the caller wants merged with the envelope every send shares.
    """
    import json as _json, urllib.error, urllib.request
    token = os.environ["WHATSAPP_TOKEN"]
    phone_id = os.environ["WHATSAPP_PHONE_ID"]
    body = _json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        **message,
    }).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{phone_id}/messages",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = _json.loads(r.read().decode() or "{}")
        # Meta answers with the message id it accepted; returning it means a send can be
        # traced in their dashboard rather than taken on trust.
        message_id = (payload.get("messages") or [{}])[0].get("id")
        return {"sent": True, "configured": True, "to": to, "status": r.status,
                "message_id": message_id, "response": payload}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            detail = _json.loads(raw).get("error", {})
        except Exception:  # noqa: BLE001
            detail = {}
        code = detail.get("code")
        explanation = _WHATSAPP_ERRORS.get(code) or detail.get("message") or raw[:400]
        logger.warning("WhatsApp refused (HTTP %s, code %s): %s", e.code, code, explanation)
        return {"sent": False, "configured": True, "to": to, "status": e.code,
                "error_code": code, "error": explanation, "response": raw[:1000]}
    except Exception as e:  # noqa: BLE001
        logger.warning("WhatsApp send failed: %s", e)
        return {"sent": False, "configured": True, "to": to, "error": str(e)}


@router.get("/reports/daily-brief")
async def daily_brief(date: Optional[str] = None, user: dict = Depends(REPORTS),
                      db: PropertyScopedDatabase = Depends(tenant_db)):
    return await build_daily_brief(db, date)


class BriefSendIn(BaseModel):
    date: Optional[str] = None
    to: Optional[str] = None  # override owner phone


@router.post("/reports/daily-brief/send")
async def daily_brief_send(payload: BriefSendIn, user: dict = Depends(REPORTS),
                           db: PropertyScopedDatabase = Depends(tenant_db)):
    brief = await build_daily_brief(db, payload.date)
    to = (payload.to or os.environ.get("OWNER_PHONE") or "").strip()
    result = await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, brief["message"])
    return {"brief": brief, "delivery": result}


class WhatsAppTestIn(BaseModel):
    to: str = ""
    message: str = ""


@router.get("/whatsapp/status")
async def whatsapp_status(user: dict = Depends(require_access(OUTLET, "admin"))):
    """Whether WhatsApp could send right now, and what is missing if it could not."""
    problem = whatsapp_config_problem()
    to = (os.environ.get("OWNER_PHONE") or "").strip()
    return {
        "configured": not problem,
        "problem": problem,
        "recipient": to,
        # Never the token itself. Enough to tell a wrong one from a missing one.
        "token_set": bool(os.environ.get("WHATSAPP_TOKEN")),
        "phone_id_set": bool(os.environ.get("WHATSAPP_PHONE_ID")),
    }


@router.post("/whatsapp/test")
async def whatsapp_test(payload: WhatsAppTestIn,
                        user: dict = Depends(require_access(OUTLET, "admin"))):
    """Send one real message and hand back exactly what Meta said.

    The point is that it cannot appear to work: no credentials returns the named
    misconfiguration, a refusal returns Meta's own code translated into what to do about
    it, and a success returns the message id you can find in their dashboard.
    """
    to = (payload.to or os.environ.get("OWNER_PHONE") or "").strip()
    text = payload.message or "BarFlow test message. If you are reading this, WhatsApp is working."
    result = await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, text)
    return result


_last_brief_sent = {"date": None}


async def send_daily_brief(day: Optional[str] = None) -> int:
    """Send the owner brief for `day` — one message per live property. Returns how many.

    The clock is the property's, not the server's: an 11pm brief must go out at 11pm
    where the bar is, and cover that same local day's trade. `day` defaults to the
    property-local today for that reason, and is only passed in by a caller that has
    already worked out which local day it is asking about.

    Nothing here belongs to a request, so there is no caller to scope from: this reads
    the tenant list from `unscoped_db` and builds each brief through a handle bound to
    that one hotel, so no property's figures can be added to another's. Suspended and
    pending hotels are skipped — a hotel that cannot trade has no day to report.

    A hotel's brief still goes to the single OWNER_PHONE, which is the operator's, not
    the hotel's. Per-hotel delivery belongs with the subscription record — the property
    name is in the message so the two are at least distinguishable meanwhile.

    Split out of `daily_brief_scheduler` so that the two deployments can each drive it
    from the clock they actually have: the container has a process that lives all night
    and can watch a clock, a function does not and is woken by Cloud Scheduler instead.
    The message and the tenant scoping are the same code either way, which is the point
    — a second copy would drift, and its first divergence would be a number in a WhatsApp
    message that nobody could reconcile against the Reports screen.
    """
    day = day or now_local().date().isoformat()
    properties = await unscoped_db.properties.find(
        {"status": LIVE}, {"_id": 0}).to_list(1000)
    to = (os.environ.get("OWNER_PHONE") or "").strip()
    for record in properties:
        brief = await build_daily_brief(PropertyScopedDatabase(record["id"]), day)
        text = f"{record.get('name') or 'Property'}\n{brief['message']}"
        await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, text)
    logger.info("[daily-brief] auto-sent for %s to %d propert(ies)", day, len(properties))
    return len(properties)


def in_process_brief_enabled() -> bool:
    """Whether *this* process should run the brief itself, on its own clock.

    Two things have to be true, and they answer different questions.

    `DAILY_BRIEF_ENABLED` is the operator's switch and always was: it is "false" in
    `render.yaml` and in `.env.example` because a nightly WhatsApp message to a real
    phone is not something a clone or a staging box should start doing on its own.

    `FUNCTION_TARGET` is the runtime's answer to "am I a Cloud Function?" — the Functions
    framework sets it to the name of the entry point it is serving, and nothing else
    does. Under Functions the brief is a `scheduler_fn.on_schedule` function that Cloud
    Scheduler wakes at OWNER_BRIEF_TIME, so an in-process loop here would be the second
    sender, not the first. It would also not work: the loop only makes progress while an
    instance is alive, and a function instance with no traffic is shut down within
    minutes, so the 23:00 tick simply never arrives. That silent stop is the bug the move
    to a scheduled function exists to fix; this check is what stops the fix from being
    undone by the code it replaced.

    The container path is untouched. `backend/Dockerfile` and `render.yaml` set no
    `FUNCTION_TARGET`, so a Cloud Run or Render deployment still starts the loop exactly
    as before, and is still the only sender there.
    """
    if os.environ.get("DAILY_BRIEF_ENABLED", "true").lower() != "true":
        return False
    return not os.environ.get("FUNCTION_TARGET")


async def daily_brief_scheduler():
    """Watch the property's clock and send the brief once, at OWNER_BRIEF_TIME.

    The container's sender. It survives only as long as the process does, which is fine
    for a container that is always up and is why Functions uses Cloud Scheduler instead
    — see `in_process_brief_enabled`, which is what keeps exactly one of the two running.
    """
    send_time = os.environ.get("OWNER_BRIEF_TIME", "23:00")
    while True:
        try:
            now = now_local()
            hhmm = now.strftime("%H:%M")
            today = now.date().isoformat()
            if hhmm == send_time and _last_brief_sent["date"] != today:
                _last_brief_sent["date"] = today
                await send_daily_brief(today)
        except Exception as e:
            logger.warning("[daily-brief] scheduler error: %s", e)
        await asyncio.sleep(30)
