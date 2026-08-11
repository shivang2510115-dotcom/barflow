"""Orders: cart building, KOT, item status, settlement."""
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import db
from security import require_access
from models.folio import FolioEntry
from services.folio import direction_for, folio_balance

router = APIRouter()

# This property's restaurant and bar share the order, menu, table and reservation
# screens, so these endpoints declare both domains: holding either one grants access.
# Declaring "restaurant" alone would lock a bar-only waiter out of the POS.
OUTLET = ("restaurant", "bar")


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
    status: Literal["open", "settled", "cancelled", "voided"] = "open"
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
    payment_method: Literal["cash", "card", "online", "room"] = "cash"
    discount: float = 0.0
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    # Required when payment_method is "room".
    folio_id: Optional[str] = None


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


@router.post("/orders/table/{table_id}/items")
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


@router.get("/orders/table/{table_id}/current")
async def current_order(table_id: str):
    table = await db.tables.find_one({"id": table_id}, {"_id": 0})
    if not table:
        raise HTTPException(404, "Table not found")
    if not table.get("current_order_id"):
        return None
    return await db.orders.find_one({"id": table["current_order_id"]}, {"_id": 0})


@router.get("/orders/kot")
async def list_kot(user: dict = Depends(require_access(OUTLET))):
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


@router.put("/orders/{order_id}/items/{item_id}/status")
async def update_item_status(order_id: str, item_id: str, payload: UpdateItemStatusIn, user: dict = Depends(require_access(OUTLET))):
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


@router.get("/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(require_access(OUTLET))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    return order


@router.post("/orders/{order_id}/settle")
async def settle_order(order_id: str, payload: SettleIn, user: dict = Depends(require_access(OUTLET, "admin", "manager", "waiter"))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    if order["status"] != "open":
        raise HTTPException(409, f"This order is already {order['status']} and cannot be settled again")
    order["discount"] = payload.discount
    order = compute_totals(order)

    # --- validation, before anything is written ---
    folio = None
    if payload.payment_method == "room":
        if not payload.folio_id:
            raise HTTPException(400, "folio_id is required when charging to a room")
        folio = await db.folios.find_one({"id": payload.folio_id}, {"_id": 0})
        if not folio:
            raise HTTPException(404, "Folio not found")
        if folio["status"] != "open":
            raise HTTPException(409, f"That folio is {folio['status']} and cannot be charged")

    order["status"] = "settled"
    order["payment_method"] = payload.payment_method
    order["customer_name"] = (payload.customer_name or "").strip() or None
    order["customer_phone"] = (payload.customer_phone or "").strip() or None
    order["settled_at"] = datetime.now(timezone.utc).isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": order})
    await db.tables.update_one({"id": order["table_id"]}, {"$set": {"status": "free", "current_order_id": None}})

    # --- after the table is freed: post the receivable ---
    if folio is not None:
        entry = FolioEntry(
            folio_id=folio["id"], kind="outlet", direction=direction_for("outlet"),
            amount=round(order["total"], 2),
            description=f"{order['table_label']} · bill {order['id'][:8]}",
            ref_order_id=order["id"], posted_by=user.get("id")).model_dump()
        await db.folio_entries.insert_one(entry)
        entries = await db.folio_entries.find(
            {"folio_id": folio["id"]}, {"_id": 0}).to_list(5000)
        await db.folios.update_one({"id": folio["id"]}, {"$set": {
            "balance": folio_balance(entries)}})

    return order


@router.delete("/orders/{order_id}/items/{item_id}")
async def remove_item(order_id: str, item_id: str, user: dict = Depends(require_access(OUTLET, "admin", "manager", "waiter"))):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    order["items"] = [i for i in order["items"] if i["id"] != item_id]
    order = compute_totals(order)
    await db.orders.update_one({"id": order_id}, {"$set": order})
    return order
