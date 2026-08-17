"""Sales reports, analytics, and the daily owner brief (WhatsApp)."""
import os
import asyncio
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import db
from security import require_access
# Outlet sales analytics cover both the restaurant and the bar, so either domain grants
# access. The hotel revenue report is a later sub-project and will declare "hotel".
from services.access import OUTLET
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
async def report_summary(user: dict = Depends(REPORTS)):
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
async def recent_orders(user: dict = Depends(REPORTS)):
    return await db.orders.find({"status": "settled"}, {"_id": 0}).sort("settled_at", -1).limit(50).to_list(50)


@router.get("/reports/analytics")
async def report_analytics(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: str = "day",
    user: dict = Depends(REPORTS),
):
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


async def build_daily_brief(date: Optional[str] = None) -> dict:
    """Compute the owner's end-of-day summary for a single date (settled_at)."""
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


def _send_whatsapp(to: str, text: str) -> dict:
    """Send via Meta WhatsApp Cloud API when creds present; else mock (return the text)."""
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID")
    if not (token and phone_id and to):
        logger.info("[daily-brief] MOCK WhatsApp send to %s:\n%s", to or "<no OWNER_PHONE>", text)
        return {"sent": False, "mock": True, "to": to, "message": text}
    import json as _json, urllib.request
    body = _json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{phone_id}/messages",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"sent": True, "mock": False, "to": to, "status": r.status}
    except Exception as e:
        logger.warning("[daily-brief] WhatsApp send failed: %s", e)
        return {"sent": False, "mock": False, "error": str(e), "to": to}


@router.get("/reports/daily-brief")
async def daily_brief(date: Optional[str] = None, user: dict = Depends(REPORTS)):
    return await build_daily_brief(date)


class BriefSendIn(BaseModel):
    date: Optional[str] = None
    to: Optional[str] = None  # override owner phone


@router.post("/reports/daily-brief/send")
async def daily_brief_send(payload: BriefSendIn, user: dict = Depends(REPORTS)):
    brief = await build_daily_brief(payload.date)
    to = (payload.to or os.environ.get("OWNER_PHONE") or "").strip()
    result = await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, brief["message"])
    return {"brief": brief, "delivery": result}


_last_brief_sent = {"date": None}


async def daily_brief_scheduler():
    """Once a day at OWNER_BRIEF_TIME, send the owner brief.

    The clock is the property's, not the server's: an 11pm brief must go out at 11pm
    where the bar is, and cover that same local day's trade.
    """
    send_time = os.environ.get("OWNER_BRIEF_TIME", "23:00")
    while True:
        try:
            now = now_local()
            hhmm = now.strftime("%H:%M")
            today = now.date().isoformat()
            if hhmm == send_time and _last_brief_sent["date"] != today:
                _last_brief_sent["date"] = today
                brief = await build_daily_brief(today)
                to = (os.environ.get("OWNER_PHONE") or "").strip()
                await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, brief["message"])
                logger.info("[daily-brief] auto-sent for %s", today)
        except Exception as e:
            logger.warning("[daily-brief] scheduler error: %s", e)
        await asyncio.sleep(30)
