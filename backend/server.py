from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import uuid
import logging
import asyncio
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

# Configure logging early
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, status
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from emergentintegrations.payments.stripe.checkout import (
    StripeCheckout,
    CheckoutSessionRequest,
)


# ----------------- DB -----------------
mongo_url = os.environ.get('MONGO_URL', 'mock')
if not mongo_url or mongo_url.startswith('mock') or mongo_url.startswith('local'):
    logger.info("Using local JSON file-based database mock...")
    from mock_db import MockMongoClient
    client = MockMongoClient(None)
    db = client[None]
else:
    logger.info("Connecting to remote MongoDB client...")
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get('DB_NAME', 'barflow')]

app = FastAPI(title="BarFlow API")
api_router = APIRouter(prefix="/api")



# ----------------- Auth utils -----------------
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ.get("JWT_SECRET", "supersecret-key-123456789")

Role = Literal["admin", "manager", "waiter", "kitchen"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_roles(*roles: str):
    async def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return checker


# ----------------- Models -----------------
class UserPublic(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: Role


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: Role = "waiter"


class TableIn(BaseModel):
    label: str
    capacity: int = 4
    zone: str = "Main"


class Table(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str
    capacity: int = 4
    zone: str = "Main"
    status: Literal["free", "occupied", "billed", "reserved"] = "free"
    current_order_id: Optional[str] = None


class ReservationIn(BaseModel):
    guest_name: str
    phone: Optional[str] = None
    party_size: int = 2
    date: str          # "YYYY-MM-DD"
    time: str          # "HH:MM"
    table_id: Optional[str] = None
    table_label: Optional[str] = None
    hold_table: bool = False
    notes: Optional[str] = None


class StatusIn(BaseModel):
    status: Literal["booked", "seated", "no_show", "cancelled"]


class Reservation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    guest_name: str
    phone: Optional[str] = None
    party_size: int = 2
    date: str
    time: str
    table_id: Optional[str] = None
    table_label: Optional[str] = None
    status: Literal["booked", "seated", "no_show", "cancelled"] = "booked"
    notes: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MenuItemIn(BaseModel):
    name: str
    category: str
    price: float
    description: str = ""
    image: str = ""
    station: Literal["bar", "kitchen"] = "bar"
    available: bool = True


class MenuItem(MenuItemIn):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class OrderItemIn(BaseModel):
    menu_item_id: str
    quantity: int = 1
    notes: str = ""


class OrderItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    menu_item_id: str
    name: str
    price: float
    quantity: int
    station: str
    notes: str = ""
    status: Literal["pending", "preparing", "ready", "served"] = "pending"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_id: str
    table_label: str
    items: List[OrderItem] = []
    status: Literal["open", "settled", "cancelled"] = "open"
    subtotal: float = 0.0
    tax: float = 0.0
    discount: float = 0.0
    total: float = 0.0
    payment_method: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    source: Literal["pos", "qr"] = "pos"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    settled_at: Optional[str] = None


class AddItemsIn(BaseModel):
    items: List[OrderItemIn]
    source: Literal["pos", "qr"] = "pos"


class SettleIn(BaseModel):
    payment_method: Literal["cash", "card", "online"] = "cash"
    discount: float = 0.0
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None


class InventoryItemIn(BaseModel):
    name: str
    unit: str = "bottle"
    stock: float = 0
    threshold: float = 5
    cost_per_unit: float = 0
    category: str = "spirits"


class InventoryItem(InventoryItemIn):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class InventoryAdjustIn(BaseModel):
    delta: float
    reason: str = ""


# ----------------- Helpers -----------------
def compute_totals(order: dict) -> dict:
    subtotal = sum(i["price"] * i["quantity"] for i in order.get("items", []))
    tax = round(subtotal * 0.10, 2)
    discount = order.get("discount", 0)
    total = round(subtotal + tax - discount, 2)
    order["subtotal"] = round(subtotal, 2)
    order["tax"] = tax
    order["total"] = total
    return order


# ----------------- Auth Routes -----------------
@api_router.post("/auth/login")
async def login(payload: LoginIn):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], user["email"], user["role"])
    return {
        "token": token,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]},
    }


@api_router.post("/auth/register")
async def register(payload: RegisterIn, current: dict = Depends(require_roles("admin"))):
    email = payload.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already exists")
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": payload.name,
        "role": payload.role,
        "password_hash": hash_password(payload.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    return {"id": doc["id"], "email": doc["email"], "name": doc["name"], "role": doc["role"]}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}


@api_router.get("/auth/staff")
async def list_staff(user: dict = Depends(require_roles("admin", "manager"))):
    docs = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(500)
    return docs


# ----------------- Tables -----------------
@api_router.get("/tables")
async def list_tables(user: dict = Depends(get_current_user)):
    return await db.tables.find({}, {"_id": 0}).sort("label", 1).to_list(500)


@api_router.get("/tables/public/{table_id}")
async def get_table_public(table_id: str):
    t = await db.tables.find_one({"id": table_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Table not found")
    return t


@api_router.post("/tables")
async def create_table(payload: TableIn, user: dict = Depends(require_roles("admin", "manager"))):
    t = Table(**payload.model_dump()).model_dump()
    await db.tables.insert_one(t)
    t.pop("_id", None)
    return t


@api_router.delete("/tables/{table_id}")
async def delete_table(table_id: str, user: dict = Depends(require_roles("admin", "manager"))):
    await db.tables.delete_one({"id": table_id})
    return {"ok": True}


# ----------------- Reservations -----------------
@api_router.get("/reservations")
async def list_reservations(date: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"date": date} if date else {}
    return await db.reservations.find(query, {"_id": 0}).sort("time", 1).to_list(500)


@api_router.post("/reservations")
async def create_reservation(
    payload: ReservationIn,
    user: dict = Depends(require_roles("admin", "manager", "waiter")),
):
    data = payload.model_dump()
    hold = data.pop("hold_table", False)
    r = Reservation(**data).model_dump()
    await db.reservations.insert_one(r)
    r.pop("_id", None)

    # Hold the physical table if requested and it's currently free.
    if hold and r.get("table_id"):
        table = await db.tables.find_one({"id": r["table_id"]}, {"_id": 0})
        if table and table.get("status") == "free":
            await db.tables.update_one(
                {"id": r["table_id"]}, {"$set": {"status": "reserved"}}
            )
    return r


@api_router.put("/reservations/{reservation_id}/status")
async def set_reservation_status(
    reservation_id: str,
    payload: StatusIn,
    user: dict = Depends(require_roles("admin", "manager", "waiter")),
):
    res = await db.reservations.find_one({"id": reservation_id}, {"_id": 0})
    if not res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    await db.reservations.update_one(
        {"id": reservation_id}, {"$set": {"status": payload.status}}
    )

    # Release a held table when the booking is done/void, but never stomp a
    # table that has since been occupied by a live order.
    if res.get("table_id") and payload.status in ("cancelled", "no_show", "seated"):
        table = await db.tables.find_one({"id": res["table_id"]}, {"_id": 0})
        if table and table.get("status") == "reserved":
            await db.tables.update_one(
                {"id": res["table_id"]}, {"$set": {"status": "free"}}
            )
    return {"ok": True, "status": payload.status}


@api_router.delete("/reservations/{reservation_id}")
async def delete_reservation(
    reservation_id: str,
    user: dict = Depends(require_roles("admin", "manager")),
):
    res = await db.reservations.find_one({"id": reservation_id}, {"_id": 0})
    if res and res.get("table_id"):
        table = await db.tables.find_one({"id": res["table_id"]}, {"_id": 0})
        if table and table.get("status") == "reserved":
            await db.tables.update_one(
                {"id": res["table_id"]}, {"$set": {"status": "free"}}
            )
    await db.reservations.delete_one({"id": reservation_id})
    return {"ok": True}


# ----------------- Menu -----------------
@api_router.get("/menu")
async def list_menu():
    return await db.menu.find({}, {"_id": 0}).sort("category", 1).to_list(1000)


@api_router.post("/menu")
async def create_menu_item(payload: MenuItemIn, user: dict = Depends(require_roles("admin", "manager"))):
    m = MenuItem(**payload.model_dump()).model_dump()
    await db.menu.insert_one(m)
    m.pop("_id", None)
    return m


@api_router.put("/menu/{item_id}")
async def update_menu_item(item_id: str, payload: MenuItemIn, user: dict = Depends(require_roles("admin", "manager"))):
    await db.menu.update_one({"id": item_id}, {"$set": payload.model_dump()})
    doc = await db.menu.find_one({"id": item_id}, {"_id": 0})
    return doc


@api_router.delete("/menu/{item_id}")
async def delete_menu_item(item_id: str, user: dict = Depends(require_roles("admin", "manager"))):
    await db.menu.delete_one({"id": item_id})
    return {"ok": True}


# ----------------- Orders -----------------
async def _get_or_create_open_order(table_id: str, source: str = "pos") -> dict:
    table = await db.tables.find_one({"id": table_id}, {"_id": 0})
    if not table:
        raise HTTPException(404, "Table not found")
    if table.get("current_order_id"):
        order = await db.orders.find_one({"id": table["current_order_id"]}, {"_id": 0})
        if order and order["status"] == "open":
            return order
    order = Order(table_id=table_id, table_label=table["label"], source=source).model_dump()
    await db.orders.insert_one(order)
    order.pop("_id", None)
    await db.tables.update_one({"id": table_id}, {"$set": {"status": "occupied", "current_order_id": order["id"]}})
    return order


@api_router.post("/orders/table/{table_id}/items")
async def add_items(table_id: str, payload: AddItemsIn):
    order = await _get_or_create_open_order(table_id, payload.source)
    new_items = []
    for it in payload.items:
        m = await db.menu.find_one({"id": it.menu_item_id}, {"_id": 0})
        if not m:
            continue
        oi = OrderItem(
            menu_item_id=m["id"], name=m["name"], price=m["price"],
            quantity=it.quantity, station=m.get("station", "bar"), notes=it.notes,
        ).model_dump()
        new_items.append(oi)
    order["items"].extend(new_items)
    order = compute_totals(order)
    await db.orders.update_one({"id": order["id"]}, {"$set": {
        "items": order["items"], "subtotal": order["subtotal"], "tax": order["tax"], "total": order["total"],
    }})
    return order


@api_router.get("/orders/table/{table_id}/current")
async def current_order(table_id: str):
    table = await db.tables.find_one({"id": table_id}, {"_id": 0})
    if not table:
        raise HTTPException(404, "Table not found")
    if not table.get("current_order_id"):
        return None
    return await db.orders.find_one({"id": table["current_order_id"]}, {"_id": 0})


@api_router.get("/orders/kot")
async def list_kot(user: dict = Depends(get_current_user)):
    """All pending/preparing items across open orders."""
    open_orders = await db.orders.find({"status": "open"}, {"_id": 0}).to_list(500)
    tickets = []
    for o in open_orders:
        pending_items = [i for i in o["items"] if i["status"] in ("pending", "preparing")]
        if pending_items:
            tickets.append({
                "order_id": o["id"],
                "table_label": o["table_label"],
                "created_at": o["created_at"],
                "items": pending_items,
            })
    return tickets


class UpdateItemStatusIn(BaseModel):
    status: Literal["pending", "preparing", "ready", "served"]


@api_router.put("/orders/{order_id}/items/{item_id}/status")
async def update_item_status(order_id: str, item_id: str, payload: UpdateItemStatusIn, user: dict = Depends(get_current_user)):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    updated = False
    for it in order["items"]:
        if it["id"] == item_id:
            it["status"] = payload.status
            updated = True
            break
    if updated:
        await db.orders.update_one({"id": order_id}, {"$set": {"items": order["items"]}})
    return order


@api_router.post("/orders/{order_id}/settle")
async def settle_order(order_id: str, payload: SettleIn, user: dict = Depends(require_roles("admin", "manager", "waiter"))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    order["discount"] = payload.discount
    order = compute_totals(order)
    order["status"] = "settled"
    order["payment_method"] = payload.payment_method
    order["customer_name"] = (payload.customer_name or "").strip() or None
    order["customer_phone"] = (payload.customer_phone or "").strip() or None
    order["settled_at"] = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": order})
    await db.tables.update_one({"id": order["table_id"]}, {"$set": {"status": "free", "current_order_id": None}})
    return order


@api_router.delete("/orders/{order_id}/items/{item_id}")
async def remove_item(order_id: str, item_id: str, user: dict = Depends(require_roles("admin", "manager", "waiter"))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    order["items"] = [i for i in order["items"] if i["id"] != item_id]
    order = compute_totals(order)
    await db.orders.update_one({"id": order_id}, {"$set": order})
    return order


# ----------------- Inventory -----------------
@api_router.get("/inventory")
async def list_inventory(user: dict = Depends(get_current_user)):
    return await db.inventory.find({}, {"_id": 0}).sort("name", 1).to_list(1000)


@api_router.post("/inventory")
async def create_inventory(payload: InventoryItemIn, user: dict = Depends(require_roles("admin", "manager"))):
    item = InventoryItem(**payload.model_dump()).model_dump()
    await db.inventory.insert_one(item)
    item.pop("_id", None)
    return item


@api_router.put("/inventory/{item_id}")
async def update_inventory(item_id: str, payload: InventoryItemIn, user: dict = Depends(require_roles("admin", "manager"))):
    await db.inventory.update_one({"id": item_id}, {"$set": payload.model_dump()})
    return await db.inventory.find_one({"id": item_id}, {"_id": 0})


@api_router.post("/inventory/{item_id}/adjust")
async def adjust_inventory(item_id: str, payload: InventoryAdjustIn, user: dict = Depends(require_roles("admin", "manager", "kitchen"))):
    item = await db.inventory.find_one({"id": item_id}, {"_id": 0})
    if not item:
        raise HTTPException(404, "Item not found")
    new_stock = max(0, item["stock"] + payload.delta)
    await db.inventory.update_one({"id": item_id}, {"$set": {"stock": new_stock}})
    return {**item, "stock": new_stock}


@api_router.delete("/inventory/{item_id}")
async def delete_inventory(item_id: str, user: dict = Depends(require_roles("admin", "manager"))):
    await db.inventory.delete_one({"id": item_id})
    return {"ok": True}


# ----------------- Reports -----------------
@api_router.get("/reports/summary")
async def report_summary(user: dict = Depends(require_roles("admin", "manager"))):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(2000)
    today_orders = [o for o in settled if o.get("settled_at", "") >= today]
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
        d = o.get("settled_at", "")[:10]
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


@api_router.get("/reports/orders")
async def recent_orders(user: dict = Depends(require_roles("admin", "manager"))):
    return await db.orders.find({"status": "settled"}, {"_id": 0}).sort("settled_at", -1).limit(50).to_list(50)


@api_router.get("/reports/analytics")
async def report_analytics(
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: str = "day",
    user: dict = Depends(require_roles("admin", "manager")),
):
    """Sales analytics for a date range (inclusive, by settled_at date).

    - granularity "day" -> YYYY-MM-DD buckets; "month" -> YYYY-MM buckets.
    - Defaults to the current month when start/end are omitted.
    - Returns range KPIs, a time series, top items, payment mix and top customers.
    """
    if granularity not in ("day", "month"):
        granularity = "day"

    today = datetime.now(timezone.utc).date()
    if not end:
        end = today.isoformat()
    if not start:
        start = today.replace(day=1).isoformat()

    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(100000)
    in_range = []
    for o in settled:
        d = (o.get("settled_at") or "")[:10]
        if d and start <= d <= end:
            in_range.append(o)

    revenue = sum((o.get("total") or 0) for o in in_range)
    orders_count = len(in_range)
    items_sold = sum(it.get("quantity", 0) for o in in_range for it in o.get("items", []))
    avg_order = (revenue / orders_count) if orders_count else 0

    # Time series (bucketed by day or month)
    buckets: dict = {}
    for o in in_range:
        d = (o.get("settled_at") or "")[:10]
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


def _money(v):
    return f"{CURRENCY_SYMBOL}{round(v or 0):,}"


async def build_daily_brief(date: Optional[str] = None) -> dict:
    """Compute the owner's end-of-day summary for a single date (settled_at)."""
    if not date:
        date = datetime.now(timezone.utc).date().isoformat()

    settled = await db.orders.find({"status": "settled"}, {"_id": 0}).to_list(100000)
    day = [o for o in settled if (o.get("settled_at") or "")[:10] == date]

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


@api_router.get("/reports/daily-brief")
async def daily_brief(date: Optional[str] = None, user: dict = Depends(require_roles("admin", "manager"))):
    return await build_daily_brief(date)


class BriefSendIn(BaseModel):
    date: Optional[str] = None
    to: Optional[str] = None  # override owner phone


@api_router.post("/reports/daily-brief/send")
async def daily_brief_send(payload: BriefSendIn, user: dict = Depends(require_roles("admin", "manager"))):
    brief = await build_daily_brief(payload.date)
    to = (payload.to or os.environ.get("OWNER_PHONE") or "").strip()
    result = await asyncio.get_event_loop().run_in_executor(None, _send_whatsapp, to, brief["message"])
    return {"brief": brief, "delivery": result}


_last_brief_sent = {"date": None}


async def daily_brief_scheduler():
    """Once a day at OWNER_BRIEF_TIME (local HH:MM), send the owner brief."""
    send_time = os.environ.get("OWNER_BRIEF_TIME", "23:00")
    while True:
        try:
            now = datetime.now()
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


# ----------------- Stripe Checkout -----------------
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "sk_test_emergent")


class CheckoutStartIn(BaseModel):
    order_id: str
    origin_url: str  # frontend origin, e.g. https://qr-bill-hub.preview.emergentagent.com


@api_router.post("/payments/checkout/session")
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


@api_router.get("/payments/checkout/status/{session_id}")
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


@api_router.post("/webhook/stripe")
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



# ----------------- Seed -----------------
async def seed_data():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.tables.create_index("label")
    await db.reservations.create_index("date")
    await db.menu.create_index("category")

    # Seed admin + staff
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@barflow.io").lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")

    default_users = [
        {"email": admin_email, "name": "Alex Mercer", "role": "admin", "password": admin_pw},
    ]

    # The manager/waiter/kitchen logins below use passwords that are published in this
    # repo. They exist so a fresh clone is usable immediately, and must never be seeded
    # on a public deployment — set DEMO_LOGINS=false there.
    if os.environ.get("DEMO_LOGINS", "true").lower() == "true":
        default_users += [
            {"email": "manager@barflow.io", "name": "Jamie Rowe", "role": "manager", "password": "manager123"},
            {"email": "waiter@barflow.io", "name": "Riley Cole", "role": "waiter", "password": "waiter123"},
            {"email": "kitchen@barflow.io", "name": "Sam Ash", "role": "kitchen", "password": "kitchen123"},
        ]

    for u in default_users:
        existing = await db.users.find_one({"email": u["email"]})
        if existing is None:
            await db.users.insert_one({
                "id": str(uuid.uuid4()),
                "email": u["email"],
                "name": u["name"],
                "role": u["role"],
                "password_hash": hash_password(u["password"]),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        # An existing account keeps whatever password it currently has. Re-hashing the
        # seed value here would silently undo every password change on each restart.

    # Seed tables
    if await db.tables.count_documents({}) == 0:
        zones = [("Bar", 6, 4), ("Lounge", 4, 4), ("Patio", 3, 6)]
        seq = 1
        for zone, count, cap in zones:
            for i in range(count):
                t = Table(label=f"T{seq:02d}", capacity=cap, zone=zone).model_dump()
                await db.tables.insert_one(t)
                seq += 1

    # Seed menu
    menu_images = {
        "Smoked Old Fashioned": "https://images.unsplash.com/photo-1536935338788-846bb9981813?w=800&q=80&auto=format&fit=crop",
        "Midnight Negroni": "https://images.unsplash.com/photo-1541546006121-5c3bc5e8c7b9?w=800&q=80&auto=format&fit=crop",
        "Copper Sour": "https://images.unsplash.com/photo-1514362545857-3bc16c4c7d1b?w=800&q=80&auto=format&fit=crop",
        "Neon Spritz": "https://images.unsplash.com/photo-1587223962930-cb7f31384c19?w=800&q=80&auto=format&fit=crop",
        "House Lager": "https://images.unsplash.com/photo-1608270586620-248524c67de9?w=800&q=80&auto=format&fit=crop",
        "Copper Ale": "https://images.unsplash.com/photo-1571613914406-6301c3b1f39f?w=800&q=80&auto=format&fit=crop",
        "Dark Stout": "https://images.unsplash.com/photo-1618885472179-5e474019f2a9?w=800&q=80&auto=format&fit=crop",
        "Malbec": "https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=800&q=80&auto=format&fit=crop",
        "Sauvignon Blanc": "https://images.unsplash.com/photo-1510812431401-41d2bd2722f3?w=800&q=80&auto=format&fit=crop",
        "Islay Single Malt": "https://images.unsplash.com/photo-1527281400683-1aae777175f8?w=800&q=80&auto=format&fit=crop",
        "Reposado Tequila": "https://images.unsplash.com/photo-1516997121675-4c2d1684aa3e?w=800&q=80&auto=format&fit=crop",
        "Truffle Fries": "https://images.unsplash.com/photo-1630384060421-cb20d0e0649d?w=800&q=80&auto=format&fit=crop",
        "Wagyu Sliders": "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=800&q=80&auto=format&fit=crop",
        "Charred Octopus": "https://images.unsplash.com/photo-1625944525200-2b241f0f6f8f?w=800&q=80&auto=format&fit=crop",
        "Bar Burger": "https://images.unsplash.com/photo-1571091718767-18b5b1457add?w=800&q=80&auto=format&fit=crop",
        "Crispy Chicken": "https://images.unsplash.com/photo-1562967914-608f82629710?w=800&q=80&auto=format&fit=crop",
    }
    if await db.menu.count_documents({}) == 0:
        menu = [
            # Cocktails
            {"name": "Smoked Old Fashioned", "category": "Cocktails", "price": 14, "station": "bar", "description": "Bourbon, applewood smoke, orange bitters."},
            {"name": "Midnight Negroni", "category": "Cocktails", "price": 13, "station": "bar", "description": "Gin, Campari, sweet vermouth, charred orange."},
            {"name": "Copper Sour", "category": "Cocktails", "price": 12, "station": "bar", "description": "Mezcal, lime, agave, egg white."},
            {"name": "Neon Spritz", "category": "Cocktails", "price": 11, "station": "bar", "description": "Aperol, prosecco, blood orange."},
            # Beer
            {"name": "House Lager", "category": "Draft Beer", "price": 7, "station": "bar", "description": "Crisp pilsner on tap."},
            {"name": "Copper Ale", "category": "Draft Beer", "price": 8, "station": "bar", "description": "Amber ale, toasted malt."},
            {"name": "Dark Stout", "category": "Draft Beer", "price": 9, "station": "bar", "description": "Rich chocolate stout."},
            # Wine
            {"name": "Malbec", "category": "Wine", "price": 12, "station": "bar", "description": "Mendoza red, glass."},
            {"name": "Sauvignon Blanc", "category": "Wine", "price": 11, "station": "bar", "description": "Crisp white, glass."},
            # Spirits
            {"name": "Islay Single Malt", "category": "Spirits", "price": 18, "station": "bar", "description": "Peated, neat pour."},
            {"name": "Reposado Tequila", "category": "Spirits", "price": 14, "station": "bar", "description": "Aged 8 months, oak."},
            # Food
            {"name": "Truffle Fries", "category": "Small Plates", "price": 9, "station": "kitchen", "description": "Hand-cut, parmesan, truffle oil."},
            {"name": "Wagyu Sliders", "category": "Small Plates", "price": 15, "station": "kitchen", "description": "Three sliders, aged cheddar."},
            {"name": "Charred Octopus", "category": "Small Plates", "price": 18, "station": "kitchen", "description": "Smoked paprika, lemon."},
            {"name": "Bar Burger", "category": "Mains", "price": 17, "station": "kitchen", "description": "Dry-aged patty, brioche, gruyère."},
            {"name": "Crispy Chicken", "category": "Mains", "price": 15, "station": "kitchen", "description": "Buttermilk, hot honey."},
        ]
        for m in menu:
            m["image"] = menu_images.get(m["name"], "")
            item = MenuItem(**m).model_dump()
            await db.menu.insert_one(item)
    else:
        # Backfill images on existing docs that don't have one
        for name, url in menu_images.items():
            await db.menu.update_one(
                {"name": name, "$or": [{"image": ""}, {"image": None}, {"image": {"$exists": False}}]},
                {"$set": {"image": url}},
            )

    # Seed inventory
    if await db.inventory.count_documents({}) == 0:
        inv = [
            {"name": "Bourbon 750ml", "unit": "bottle", "stock": 12, "threshold": 4, "cost_per_unit": 35, "category": "spirits"},
            {"name": "Gin 750ml", "unit": "bottle", "stock": 8, "threshold": 4, "cost_per_unit": 28, "category": "spirits"},
            {"name": "Mezcal 750ml", "unit": "bottle", "stock": 3, "threshold": 4, "cost_per_unit": 42, "category": "spirits"},
            {"name": "Malbec Case", "unit": "case", "stock": 5, "threshold": 2, "cost_per_unit": 120, "category": "wine"},
            {"name": "House Lager Keg", "unit": "keg", "stock": 4, "threshold": 2, "cost_per_unit": 180, "category": "beer"},
            {"name": "Dark Stout Keg", "unit": "keg", "stock": 1, "threshold": 2, "cost_per_unit": 210, "category": "beer"},
            {"name": "Wagyu Beef", "unit": "kg", "stock": 8, "threshold": 5, "cost_per_unit": 90, "category": "food"},
            {"name": "Potatoes", "unit": "kg", "stock": 22, "threshold": 10, "cost_per_unit": 3, "category": "food"},
            {"name": "Aperol 750ml", "unit": "bottle", "stock": 2, "threshold": 3, "cost_per_unit": 26, "category": "spirits"},
        ]
        for i in inv:
            item = InventoryItem(**i).model_dump()
            await db.inventory.insert_one(item)


# ----------------- Startup -----------------
@app.on_event("startup")
async def on_startup():
    await seed_data()
    logger.info("Seed complete.")
    if os.environ.get("DAILY_BRIEF_ENABLED", "true").lower() == "true":
        asyncio.create_task(daily_brief_scheduler())
        logger.info("Daily brief scheduler started (%s).", os.environ.get("OWNER_BRIEF_TIME", "23:00"))


@api_router.get("/")
async def root():
    return {"service": "BarFlow API", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
